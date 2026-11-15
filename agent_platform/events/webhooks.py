"""Signed, idempotent webhook delivery primitives."""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit


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
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("webhook URL must be an absolute HTTPS URL without credentials or localhost")
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
    async def complete(self, delivery_id: str, *, response_code: int) -> None: ...
    async def retry(self, delivery_id: str, *, next_attempt_at: datetime,
                    response_code: int | None) -> None: ...
    async def dead_letter(self, delivery_id: str, *, response_code: int | None) -> None: ...


def signature(secret: bytes, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def verify_signature(secret: bytes, timestamp: str, body: bytes, supplied: str) -> bool:
    if not timestamp.isdigit() or len(timestamp) > 12:
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
                    await self.store.complete(attempt.delivery_id, response_code=code)
                    result["delivered"] += 1
                    continue
            except Exception:
                code = None
            if attempt.attempt >= self.max_attempts:
                await self.store.dead_letter(attempt.delivery_id, response_code=code)
                result["dead_lettered"] += 1
            else:
                delay = min(3600, 2 ** min(attempt.attempt, 10))
                await self.store.retry(
                    attempt.delivery_id, next_attempt_at=self.clock() + timedelta(seconds=delay),
                    response_code=code,
                )
                result["retrying"] += 1
        return result
