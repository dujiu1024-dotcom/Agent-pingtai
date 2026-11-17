"""Signed, idempotent webhook delivery primitives."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import ipaddress
import json
import socket
import time
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
    lease_token: str = ""
    response_code: int | None = None


class WebhookTransport(Protocol):
    async def post_pinned(
        self, url: str, *, resolved_addresses: tuple[str, ...], body: bytes,
        headers: Mapping[str, str], timeout_seconds: float,
    ) -> int: ...


class WebhookEgressPolicy:
    """Resolve each destination and pass the exact public IP set to the transport.

    Production transports must connect only to one of the supplied addresses
    while preserving the original hostname for TLS SNI and certificate checks.
    """

    def __init__(self, resolver: Callable[[str, int], Any] | None = None) -> None:
        self.resolver = resolver or self._system_resolver

    @staticmethod
    async def _system_resolver(host: str, port: int) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()
        rows = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return tuple(sorted({row[4][0] for row in rows}))

    async def resolve_public_addresses(self, url: str) -> tuple[str, ...]:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("webhook destination must be HTTPS")
        host = parsed.hostname
        port = parsed.port or 443
        try:
            literal = ipaddress.ip_address(host)
            addresses = (str(literal),)
        except ValueError:
            resolved = self.resolver(host, port)
            if hasattr(resolved, "__await__"):
                resolved = await resolved
            addresses = tuple(sorted({str(ipaddress.ip_address(item)) for item in resolved}))
        if not addresses:
            raise ValueError("webhook destination did not resolve")
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("webhook destination resolves to a non-public address")
        return addresses


class WebhookStore(Protocol):
    async def claim(self, *, limit: int) -> list[tuple[WebhookSubscription, WebhookPayload, WebhookAttempt]]: ...
    async def complete(self, tenant_id: str, delivery_id: str, *, response_code: int,
                       lease_token: str) -> bool: ...
    async def retry(self, tenant_id: str, delivery_id: str, *, next_attempt_at: datetime,
                    response_code: int | None, lease_token: str,
                    last_error_code: str | None) -> bool: ...
    async def dead_letter(self, tenant_id: str, delivery_id: str, *, response_code: int | None,
                          lease_token: str, last_error_code: str | None) -> bool: ...


def signature(secret: bytes, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def verify_signature(secret: bytes, timestamp: str, body: bytes, supplied: str, *,
                      now: int | None = None, tolerance_seconds: int = 300) -> bool:
    """Verify HMAC and enforce a bounded timestamp window to prevent replay."""
    if (
        not isinstance(secret, bytes)
        or len(secret) < 32
        or not isinstance(body, bytes)
        or not isinstance(supplied, str)
        or len(supplied) != 71
        or not supplied.startswith("sha256=")
        or any(character not in "0123456789abcdef" for character in supplied[7:])
        or not isinstance(timestamp, str)
        or not timestamp
        or not timestamp.isascii()
        or not timestamp.isdigit()
        or len(timestamp) > 12
        or isinstance(tolerance_seconds, bool)
        or not isinstance(tolerance_seconds, int)
        or not 1 <= tolerance_seconds <= 900
        or (now is not None and (
            isinstance(now, bool) or not isinstance(now, int) or now < 0
        ))
    ):
        return False
    verification_time = int(time.time()) if now is None else now
    if abs(verification_time - int(timestamp)) > tolerance_seconds:
        return False
    return hmac.compare_digest(signature(secret, timestamp, body), supplied)


class WebhookDispatcher:
    """Deliver tenant events with a durable system-actor audit trail.

    The write-ahead audit record is persisted before the external HTTP request;
    audit storage failure therefore prevents delivery rather than creating an
    untraceable side effect.
    """

    SYSTEM_ACTOR_ID = "system:webhook-dispatcher"

    def __init__(self, *, store: WebhookStore, transport: WebhookTransport,
                 resolve_secret: Callable[[str], Any], egress_policy: WebhookEgressPolicy,
                 audit_sink: Any,
                 max_attempts: int = 8, timeout_seconds: float = 10.0,
                 clock: Callable[[], datetime] | None = None) -> None:
        if not 1 <= max_attempts <= 20 or not 0 < timeout_seconds <= 60:
            raise ValueError("invalid webhook retry or timeout policy")
        self.store = store
        self.transport = transport
        self.resolve_secret = resolve_secret
        if not callable(getattr(audit_sink, "record", None)):
            raise TypeError("webhook dispatcher requires an audit sink with record()")
        self.egress_policy = egress_policy
        self.audit_sink = audit_sink
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def dispatch(self, *, limit: int = 50) -> dict[str, int]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        records = await self.store.claim(limit=limit)
        result = {
            "claimed": len(records), "delivered": 0, "retrying": 0,
            "dead_lettered": 0, "lease_lost": 0,
        }
        for subscription, payload, attempt in records:
            body = json.dumps(
                {"id": payload.event_id, "type": payload.topic, "tenant_id": payload.tenant_id,
                 "data": dict(payload.body)},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            timestamp = str(int(self.clock().timestamp()))
            # System identity is explicit: delivery is asynchronous and must
            # not impersonate the user who originally emitted the event.
            await self.audit_sink.record(
                tenant_id=attempt.tenant_id,
                actor_id=self.SYSTEM_ACTOR_ID,
                action="webhook.delivery.attempt",
                subject_id=attempt.delivery_id,
                outcome="succeeded",
                detail={
                    "event_id": payload.event_id,
                    "subscription_id": subscription.subscription_id,
                    "topic": payload.topic,
                    "attempt": attempt.attempt,
                },
            )
            failure_type: str | None = None
            try:
                secret = self.resolve_secret(subscription.secret_ref)
                if inspect.isawaitable(secret):
                    secret = await secret
                if not isinstance(secret, bytes) or len(secret) < 32:
                    raise ValueError("webhook signing secret must contain at least 32 bytes")
                resolved_addresses = await self.egress_policy.resolve_public_addresses(
                    subscription.url,
                )
                code = await self.transport.post_pinned(
                    subscription.url, resolved_addresses=resolved_addresses, body=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Platform-Event-Id": payload.event_id,
                        "X-Platform-Signature": signature(secret, timestamp, body),
                        "X-Platform-Timestamp": timestamp,
                        "Idempotency-Key": payload.event_id,
                    },
                    timeout_seconds=self.timeout_seconds,
                )
            except Exception as exc:
                failure_type = type(exc).__name__
                code = None
            if code is not None and 200 <= code < 300:
                # Do not reinterpret an audit persistence failure after a
                # successful remote delivery as a transport failure/retry.
                completed = await self.store.complete(
                    attempt.tenant_id, attempt.delivery_id, response_code=code,
                    lease_token=attempt.lease_token,
                )
                if not completed:
                    await self._audit_lease_lost(subscription, payload, attempt)
                    result["lease_lost"] += 1
                    continue
                await self.audit_sink.record(
                    tenant_id=attempt.tenant_id,
                    actor_id=self.SYSTEM_ACTOR_ID,
                    action="webhook.delivery",
                    subject_id=attempt.delivery_id,
                    outcome="succeeded",
                    detail={
                        "event_id": payload.event_id,
                        "subscription_id": subscription.subscription_id,
                        "topic": payload.topic,
                        "attempt": attempt.attempt,
                        "response_code": code,
                    },
                )
                result["delivered"] += 1
                continue
            if attempt.attempt >= self.max_attempts:
                transitioned = await self.store.dead_letter(
                    attempt.tenant_id, attempt.delivery_id, response_code=code,
                    lease_token=attempt.lease_token,
                    last_error_code=failure_type or "non_success_response",
                )
                if not transitioned:
                    await self._audit_lease_lost(subscription, payload, attempt)
                    result["lease_lost"] += 1
                    continue
                result["dead_lettered"] += 1
            else:
                delay = min(3600, 2 ** min(attempt.attempt, 10))
                transitioned = await self.store.retry(
                    attempt.tenant_id, attempt.delivery_id, next_attempt_at=self.clock() + timedelta(seconds=delay),
                    response_code=code,
                    lease_token=attempt.lease_token,
                    last_error_code=failure_type or "non_success_response",
                )
                if not transitioned:
                    await self._audit_lease_lost(subscription, payload, attempt)
                    result["lease_lost"] += 1
                    continue
                result["retrying"] += 1
            await self.audit_sink.record(
                tenant_id=attempt.tenant_id,
                actor_id=self.SYSTEM_ACTOR_ID,
                action="webhook.delivery",
                subject_id=attempt.delivery_id,
                outcome="failed",
                detail={
                    "event_id": payload.event_id,
                    "subscription_id": subscription.subscription_id,
                    "topic": payload.topic,
                    "attempt": attempt.attempt,
                    "response_code": code,
                    "error_type": failure_type or "non_success_response",
                    "dead_lettered": attempt.attempt >= self.max_attempts,
                },
            )
        return result

    async def _audit_lease_lost(
        self, subscription: WebhookSubscription, payload: WebhookPayload,
        attempt: WebhookAttempt,
    ) -> None:
        await self.audit_sink.record(
            tenant_id=attempt.tenant_id,
            actor_id=self.SYSTEM_ACTOR_ID,
            action="webhook.delivery.lease_lost",
            subject_id=attempt.delivery_id,
            outcome="failed",
            detail={
                "event_id": payload.event_id,
                "subscription_id": subscription.subscription_id,
                "topic": payload.topic,
                "attempt": attempt.attempt,
            },
        )


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
                    (UUID(payload.event_id), payload.topic,
                     json.dumps(dict(payload.body), ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False),
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
                if old_secret_ref != secret_ref:
                    await connection.execute(
                        """INSERT INTO platform_webhook_secret_revocations
                           (tenant_id,subscription_id,secret_ref)
                           VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (tenant_id, UUID(subscription_id), old_secret_ref),
                    )
                await connection.execute(
                    """UPDATE platform_webhook_subscriptions
                       SET secret_ref=%s,updated_at=now()
                       WHERE tenant_id=%s AND subscription_id=%s AND status='active'""",
                    (secret_ref, tenant_id, UUID(subscription_id)),
                )
        return old_secret_ref

    async def disable_subscription(self, tenant_id: str, subscription_id: str) -> str | None:
        """Disable once but return the secret reference on retries until it is revoked.

        Secret revocation is an external side effect and cannot share the database
        transaction. Returning the reference for an already-disabled subscription
        lets the service retry a failed revocation without re-enabling delivery.
        SecretManager.revoke must therefore be idempotent.
        """
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT secret_ref,status FROM platform_webhook_subscriptions
                       WHERE tenant_id=%s AND subscription_id=%s FOR UPDATE""",
                    (tenant_id, UUID(subscription_id)),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                await connection.execute(
                    """INSERT INTO platform_webhook_secret_revocations
                       (tenant_id,subscription_id,secret_ref)
                       VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (tenant_id, UUID(subscription_id), row["secret_ref"]),
                )
                if row["status"] == "active":
                    await connection.execute(
                        """UPDATE platform_webhook_subscriptions
                           SET status='disabled',updated_at=now()
                           WHERE tenant_id=%s AND subscription_id=%s AND status='active'""",
                        (tenant_id, UUID(subscription_id)),
                    )
                return row["secret_ref"]

    async def list_pending_secret_revocations(self, *, limit: int = 100) -> list[dict[str, str]]:
        if isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,subscription_id,secret_ref
                   FROM platform_webhook_secret_revocations
                   ORDER BY created_at LIMIT %s""", (limit,),
            )
            rows = await cursor.fetchall()
        return [{
            "tenant_id": row["tenant_id"],
            "subscription_id": str(row["subscription_id"]),
            "secret_ref": row["secret_ref"],
        } for row in rows]

    async def complete_secret_revocation(self, tenant_id: str, subscription_id: str, *, secret_ref: str) -> bool:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """DELETE FROM platform_webhook_secret_revocations
                       WHERE tenant_id=%s AND subscription_id=%s AND secret_ref=%s
                       RETURNING secret_ref""",
                    (tenant_id, UUID(subscription_id), secret_ref),
                )
                return await cursor.fetchone() is not None

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
                         d.attempts,d.lease_token,s.subscription_id,s.target_url,s.secret_ref,s.topics""",
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
                next_attempt_at=None, lease_token=str(row["lease_token"]),
            )
            result.append((subscription, payload, attempt))
        return result

    async def complete(self, tenant_id: str, delivery_id: str, *, response_code: int,
                       lease_token: str) -> bool:
        return await self._update(
            tenant_id, delivery_id, "delivered", response_code=response_code,
            lease_token=lease_token, last_error_code=None,
        )

    async def retry(self, tenant_id: str, delivery_id: str, *, next_attempt_at: datetime,
                    response_code: int | None, lease_token: str,
                    last_error_code: str | None) -> bool:
        return await self._update(
            tenant_id, delivery_id, "retrying", response_code=response_code,
            next_attempt_at=next_attempt_at, lease_token=lease_token,
            last_error_code=last_error_code,
        )

    async def dead_letter(self, tenant_id: str, delivery_id: str, *, response_code: int | None,
                          lease_token: str, last_error_code: str | None) -> bool:
        return await self._update(
            tenant_id, delivery_id, "dead_lettered", response_code=response_code,
            lease_token=lease_token, last_error_code=last_error_code,
        )


    async def replay_dead_letter(
        self, tenant_id: str, delivery_id: str, *, reason: str
    ) -> bool:
        """Requeue one dead-lettered delivery without changing its event identity."""
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(delivery_id, str) or not delivery_id.strip():
            raise ValueError("delivery_id is required")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 256:
            raise ValueError("replay reason must contain 1 to 256 characters")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_webhook_deliveries d
                       SET status='pending', attempts=0, response_code=NULL,
                           next_attempt_at=NULL, lease_token=NULL, lease_until=NULL
                       FROM platform_webhook_subscriptions s
                       WHERE d.tenant_id=%s AND d.delivery_id=%s
                         AND d.status='dead_lettered'
                         AND s.tenant_id=d.tenant_id
                         AND s.subscription_id=d.subscription_id
                         AND s.status='active' AND d.topic=ANY(s.topics)
                       RETURNING d.delivery_id""",
                    (tenant_id, UUID(delivery_id)),
                )
                return await cursor.fetchone() is not None

    async def _update(self, tenant_id: str, delivery_id: str, status: str, *,
                      response_code: int | None, lease_token: str,
                      last_error_code: str | None,
                      next_attempt_at: datetime | None = None) -> bool:
        if not isinstance(lease_token, str) or not lease_token:
            raise ValueError("lease_token is required to finalize a webhook delivery")
        try:
            parsed_lease_token = UUID(lease_token)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("lease_token must be a UUID") from exc
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_webhook_deliveries
                       SET status=%s,response_code=%s,next_attempt_at=%s,last_error_code=%s,
                           delivered_at=CASE WHEN %s='delivered' THEN now() ELSE delivered_at END,
                           lease_token=NULL,lease_until=NULL
                       WHERE tenant_id=%s AND delivery_id=%s AND status='leased'
                         AND lease_token=%s""",
                    (status,response_code,next_attempt_at,last_error_code,status,
                     tenant_id,UUID(delivery_id),parsed_lease_token),
                )
                return cursor.rowcount > 0
