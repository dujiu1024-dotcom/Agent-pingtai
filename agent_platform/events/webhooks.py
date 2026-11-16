"""Signed, idempotent webhook delivery primitives."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit
from uuid import UUID


@dataclass(frozen=True)
class WebhookSubscription:
    subscription_id: str
    tenant_id: str
    url: str
    secret_ref: str
    topics: tuple[str, ...]
    active: bool = True

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        hostname = (parsed.hostname or "").lower()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if (
            parsed.scheme != "https" or not hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal"))
            or (address is not None and (
                address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_unspecified
            ))
        ):
            raise ValueError("webhook URL must be public HTTPS without credentials or private targets")
        if not self.secret_ref or not self.topics:
            raise ValueError("secret_ref and at least one topic are required")


@dataclass(frozen=True)
class WebhookPayload:
    tenant_id: str
    event_id: str
    topic: str
    body: Mapping[str, Any]


@dataclass(frozen=True)
class WebhookAttempt:
    tenant_id: str
    delivery_id: str
    event_id: str
    attempt: int
    status: str
    next_attempt_at: datetime | None
    response_code: int | None = None


class WebhookTransport(Protocol):
    async def post(self, url: str, *, body: bytes, headers: Mapping[str, str],
                   timeout_seconds: float) -> int: ...


class WebhookStore(Protocol):
    async def claim(self, *, limit: int) -> list[tuple[WebhookSubscription, WebhookPayload, WebhookAttempt]]: ...
    async def complete(self, tenant_id: str, delivery_id: str, *, response_code: int) -> None: ...
    async def retry(self, tenant_id: str, delivery_id: str, *, next_attempt_at: datetime,
                    response_code: int | None) -> None: ...
    async def dead_letter(self, tenant_id: str, delivery_id: str, *, response_code: int | None) -> None: ...


def signature(secret: bytes, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def verify_signature(secret: bytes, timestamp: str, body: bytes, supplied: str, *,
                      now: int | None = None, tolerance_seconds: int = 300) -> bool:
    if not timestamp.isdigit() or len(timestamp) > 12 or tolerance_seconds < 0:
        return False
    if now is not None and abs(now - int(timestamp)) > tolerance_seconds:
        return False
    return hmac.compare_digest(signature(secret, timestamp, body), supplied)


class WebhookDispatcher:
    def __init__(self, *, store: WebhookStore, transport: WebhookTransport,
                 resolve_secret: Callable[[str], bytes], max_attempts: int = 8,
                 timeout_seconds: float = 10.0,
                 clock: Callable[[], datetime] | None = None) -> None:
        if not 1 <= max_attempts <= 20 or not 0 < timeout_seconds <= 60:
            raise ValueError("invalid webhook retry or timeout policy")
        self.store = store
        self.transport = transport
        self.resolve_secret = resolve_secret
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def dispatch(self, *, limit: int = 50) -> dict[str, int]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        records = await self.store.claim(limit=limit)
        result = {"claimed": len(records), "delivered": 0, "retrying": 0, "dead_lettered": 0}
        for subscription, payload, attempt in records:
            body = json.dumps(
                {"id": payload.event_id, "type": payload.topic, "tenant_id": payload.tenant_id,
                 "data": dict(payload.body)},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            timestamp = str(int(self.clock().timestamp()))
            try:
                secret = self.resolve_secret(subscription.secret_ref)
                if not isinstance(secret, bytes) or len(secret) < 32:
                    raise ValueError("webhook signing secret must contain at least 32 bytes")
                code = await self.transport.post(
                    subscription.url, body=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Platform-Event-Id": payload.event_id,
                        "X-Platform-Signature": signature(secret, timestamp, body),
                        "X-Platform-Timestamp": timestamp,
                        "Idempotency-Key": payload.event_id,
                    },
                    timeout_seconds=self.timeout_seconds,
                )
                if 200 <= code < 300:
                    await self.store.complete(attempt.tenant_id, attempt.delivery_id, response_code=code)
                    result["delivered"] += 1
                    continue
            except Exception:
                code = None
            if attempt.attempt >= self.max_attempts:
                await self.store.dead_letter(attempt.tenant_id, attempt.delivery_id, response_code=code)
                result["dead_lettered"] += 1
            else:
                delay = min(3600, 2 ** min(attempt.attempt, 10))
                await self.store.retry(
                    attempt.tenant_id, attempt.delivery_id, next_attempt_at=self.clock() + timedelta(seconds=delay),
                    response_code=code,
                )
                result["retrying"] += 1
        return result


class PostgresWebhookStore:
    """Shared PostgreSQL lease/receipt store; secrets remain external secret references."""

    def __init__(self, connection_factory: Callable[[], Any], *, lease_seconds: int = 60) -> None:
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least 5")
        self._connect = connection_factory
        self.lease_seconds = lease_seconds

    async def enqueue(self, payload: WebhookPayload) -> int:
        """Fan out an outbox event to active subscriptions; unique keys deduplicate retries."""
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """INSERT INTO platform_webhook_deliveries
                       (tenant_id,delivery_id,subscription_id,event_id,topic,payload,status)
                       SELECT s.tenant_id,gen_random_uuid(),s.subscription_id,%s,%s,%s,'pending'
                       FROM platform_webhook_subscriptions s
                       WHERE s.tenant_id=%s AND s.status='active' AND %s=ANY(s.topics)
                       ON CONFLICT (tenant_id,subscription_id,event_id) DO NOTHING
                       RETURNING delivery_id""",
                    (UUID(payload.event_id), payload.topic, dict(payload.body),
                     payload.tenant_id, payload.topic),
                )
                rows = await cursor.fetchall()
        return len(rows)

    async def create_subscription(self, subscription: WebhookSubscription, *, created_by: str) -> None:
        if not created_by:
            raise ValueError("created_by is required")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_webhook_subscriptions
                       (tenant_id,subscription_id,target_url,secret_ref,topics,status,created_by)
                       VALUES (%s,%s,%s,%s,%s,'active',%s)""",
                    (subscription.tenant_id, UUID(subscription.subscription_id),
                     subscription.url, subscription.secret_ref, list(subscription.topics), created_by),
                )

    async def list_subscriptions(self, tenant_id: str) -> list[dict[str, Any]]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT subscription_id,target_url,topics,status,created_at,updated_at
                   FROM platform_webhook_subscriptions
                   WHERE tenant_id=%s ORDER BY created_at DESC""",
                (tenant_id,),
            )
            rows = await cursor.fetchall()
        return [{
            "subscription_id": str(row["subscription_id"]), "url": row["target_url"],
            "topics": tuple(row["topics"]), "status": row["status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        } for row in rows]

    async def update_secret_ref(self, tenant_id: str, subscription_id: str, *, secret_ref: str) -> str | None:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT secret_ref FROM platform_webhook_subscriptions
                       WHERE tenant_id=%s AND subscription_id=%s AND status='active'
                       FOR UPDATE""",
                    (tenant_id, UUID(subscription_id)),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                old_secret_ref = row["secret_ref"]
                await connection.execute(
                    """UPDATE platform_webhook_subscriptions
                       SET secret_ref=%s,updated_at=now()
                       WHERE tenant_id=%s AND subscription_id=%s AND status='active'""",
                    (secret_ref, tenant_id, UUID(subscription_id)),
                )
        return old_secret_ref

    async def disable_subscription(self, tenant_id: str, subscription_id: str) -> bool:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_webhook_subscriptions
                       SET status='disabled',updated_at=now()
                       WHERE tenant_id=%s AND subscription_id=%s AND status='active'
                       RETURNING secret_ref""",
                    (tenant_id, UUID(subscription_id)),
                )
                row = await cursor.fetchone()
                return row["secret_ref"] if row else None

    async def claim(self, *, limit: int) -> list[tuple[WebhookSubscription, WebhookPayload, WebhookAttempt]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        from uuid import uuid4
        lease = uuid4()
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """WITH due AS (
                         SELECT d.tenant_id, d.delivery_id
                         FROM platform_webhook_deliveries d
                         JOIN platform_webhook_subscriptions s
                           ON s.tenant_id=d.tenant_id AND s.subscription_id=d.subscription_id
                         WHERE s.status='active' AND d.topic=ANY(s.topics)
                           AND d.status IN ('pending','retrying','leased')
                           AND (d.lease_until IS NULL OR d.lease_until < now())
                           AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= now())
                         ORDER BY d.created_at LIMIT %s FOR UPDATE OF d SKIP LOCKED
                       )
                       UPDATE platform_webhook_deliveries d
                       SET status='leased', attempts=d.attempts+1,
                           lease_token=%s, lease_until=now()+make_interval(secs => %s)
                       FROM due, platform_webhook_subscriptions s
                       WHERE d.tenant_id=due.tenant_id AND d.delivery_id=due.delivery_id
                         AND s.tenant_id=d.tenant_id AND s.subscription_id=d.subscription_id
                         AND s.status='active' AND d.topic=ANY(s.topics)
                       RETURNING d.tenant_id,d.delivery_id,d.event_id,d.topic,d.payload,
                         d.attempts,s.subscription_id,s.target_url,s.secret_ref,s.topics""",
                    (limit, lease, self.lease_seconds),
                )
                rows = await cursor.fetchall()
        result = []
        for row in rows:
            tenant_id = row["tenant_id"]
            subscription = WebhookSubscription(
                subscription_id=str(row["subscription_id"]), tenant_id=tenant_id,
                url=row["target_url"], secret_ref=row["secret_ref"],
                topics=tuple(row["topics"]),
            )
            payload = WebhookPayload(
                tenant_id=tenant_id, event_id=str(row["event_id"]),
                topic=row["topic"], body=dict(row["payload"]),
            )
            attempt = WebhookAttempt(
                tenant_id=tenant_id, delivery_id=str(row["delivery_id"]),
                event_id=str(row["event_id"]), attempt=row["attempts"], status="leased",
                next_attempt_at=None,
            )
            result.append((subscription, payload, attempt))
        return result

    async def complete(self, tenant_id: str, delivery_id: str, *, response_code: int) -> None:
        await self._update(tenant_id, delivery_id, "delivered", response_code=response_code)

    async def retry(self, tenant_id: str, delivery_id: str, *, next_attempt_at: datetime,
                    response_code: int | None) -> None:
        await self._update(tenant_id, delivery_id, "retrying", response_code=response_code,
                           next_attempt_at=next_attempt_at)

    async def dead_letter(self, tenant_id: str, delivery_id: str, *, response_code: int | None) -> None:
        await self._update(tenant_id, delivery_id, "dead_lettered", response_code=response_code)

    async def _update(self, tenant_id: str, delivery_id: str, status: str, *,
                      response_code: int | None,
                      next_attempt_at: datetime | None = None) -> None:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """UPDATE platform_webhook_deliveries
                       SET status=%s,response_code=%s,next_attempt_at=%s,
                           delivered_at=CASE WHEN %s='delivered' THEN now() ELSE delivered_at END,
                           lease_token=NULL,lease_until=NULL
                       WHERE tenant_id=%s AND delivery_id=%s AND status='leased'""",
                    (status,response_code,next_attempt_at,status,tenant_id,UUID(delivery_id)),
                )
