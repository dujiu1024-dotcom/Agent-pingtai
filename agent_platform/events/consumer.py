"""At-least-once Redis Streams consumer with durable retries and dead letters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol
from uuid import UUID

from agent_platform.events.receipts import EventReceiptClaim
from agent_platform.observability.instrumentation import safe_observation, safe_update


@dataclass(frozen=True)
class EventEnvelope:
    tenant_id: str
    topic: str
    event_id: str
    message_id: str
    payload: Mapping[str, Any]


class ReceiptStore(Protocol):
    async def claim(self, *, tenant_id: str, consumer_name: str, event_id: str) -> EventReceiptClaim | None: ...
    async def is_completed(self, *, tenant_id: str, consumer_name: str, event_id: str) -> bool: ...
    async def complete(self, claim: EventReceiptClaim) -> bool: ...
    async def fail(self, claim: EventReceiptClaim, error: str) -> bool: ...
    async def dead_letter(self, claim: EventReceiptClaim, error: str) -> bool: ...


class StreamTransport(Protocol):
    async def read_group(self, **kwargs: Any) -> list[tuple[str, dict[str, str]]]: ...
    async def claim_pending(self, **kwargs: Any) -> list[tuple[str, dict[str, str]]]: ...
    async def acknowledge(self, **kwargs: Any) -> int: ...
    async def dead_letter(self, **kwargs: Any) -> str: ...
    async def pending_summary(self, **kwargs: Any) -> dict[str, Any]: ...


EventHandler = Callable[[EventEnvelope], Awaitable[None]]


class RedisStreamsConsumer:
    def __init__(
        self, *, transport: StreamTransport, receipts: ReceiptStore,
        tenant_id: str, topic: str, group: str, consumer_name: str,
        handler: EventHandler, max_attempts: int = 5, observer: Any | None = None,
    ) -> None:
        for name, value in (("tenant_id", tenant_id), ("topic", topic), ("group", group), ("consumer_name", consumer_name)):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if max_attempts < 1 or max_attempts > 100:
            raise ValueError("max_attempts must be between 1 and 100")
        self.transport = transport
        self.receipts = receipts
        self.tenant_id = tenant_id
        self.topic = topic
        self.group = group
        self.consumer_name = consumer_name
        self.handler = handler
        self.max_attempts = max_attempts
        self.observer = observer

    async def _dead_letter(
        self, *, message_id: str, fields: Mapping[str, str], event_id: str | None,
        reason: str, claim: EventReceiptClaim | None = None,
    ) -> bool:
        # Persist the original message in Redis DLQ before changing the receipt
        # or considering the source entry acknowledged. A Redis failure therefore
        # leaves the source pending; a later DB failure can at worst duplicate DLQ.
        await self.transport.dead_letter(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            message_id=message_id, fields=dict(fields), event_id=event_id or "",
            reason=reason[:1000], consumer_name=self.consumer_name,
        )
        if claim is not None:
            await self.receipts.dead_letter(claim, reason)
        return True

    async def consume_batch(
        self, *, count: int = 20, block_ms: int = 1000, pending_idle_ms: int = 30_000
    ) -> dict[str, int]:
        pending = await self.transport.claim_pending(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            consumer=self.consumer_name, min_idle_ms=pending_idle_ms, count=count,
        )
        fresh = await self.transport.read_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            consumer=self.consumer_name, count=count, block_ms=block_ms,
        )
        messages = list({message_id: fields for message_id, fields in pending + fresh}.items())
        result = {"read": len(messages), "processed": 0, "duplicates": 0, "busy": 0, "failed": 0, "dead_lettered": 0}
        for message_id, fields in messages:
            try:
                event_id = str(UUID(fields["event_id"]))
                payload = json.loads(fields["payload"])
                if not isinstance(payload, dict):
                    raise ValueError("event payload must be a JSON object")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                try:
                    await self._dead_letter(
                        message_id=message_id, fields=fields, event_id=fields.get("event_id"),
                        reason=f"invalid envelope: {type(exc).__name__}",
                    )
                    result["dead_lettered"] += 1
                except Exception:
                    result["failed"] += 1
                continue

            claim = await self.receipts.claim(
                tenant_id=self.tenant_id, consumer_name=self.consumer_name, event_id=event_id
            )
            if claim is None:
                if await self.receipts.is_completed(
                    tenant_id=self.tenant_id, consumer_name=self.consumer_name, event_id=event_id
                ):
                    await self.transport.acknowledge(
                        tenant_id=self.tenant_id, topic=self.topic, group=self.group, message_id=message_id
                    )
                    result["duplicates"] += 1
                else:
                    result["busy"] += 1
                continue

            if claim.attempts > self.max_attempts:
                try:
                    if await self._dead_letter(
                        message_id=message_id, fields=fields, event_id=event_id,
                        reason="max attempts exceeded after repeated worker interruption",
                        claim=claim,
                    ):
                        result["dead_lettered"] += 1
                    else:
                        result["failed"] += 1
                except Exception:
                    result["failed"] += 1
                continue

            envelope = EventEnvelope(
                tenant_id=self.tenant_id, topic=self.topic, event_id=event_id,
                message_id=message_id, payload=payload,
            )
            try:
                # Trace metadata deliberately excludes payload content and credentials.
                with safe_observation(
                    self.observer,
                    name="event.consume",
                    input={"topic": self.topic, "event_id": event_id},
                    metadata={
                        "tenant_id": self.tenant_id,
                        "group": self.group,
                        "consumer": self.consumer_name,
                        "message_id": message_id,
                    },
                ) as span:
                    try:
                        # Handlers must use event_id as an idempotency key for external side effects.
                        await self.handler(envelope)
                    except Exception as exc:
                        safe_update(span, output={
                            "status": "failed", "error_type": type(exc).__name__,
                        })
                        raise
                    safe_update(span, output={"status": "succeeded"})
            except Exception as exc:
                error = type(exc).__name__
                if claim.attempts >= self.max_attempts:
                    try:
                        if await self._dead_letter(
                            message_id=message_id, fields=fields, event_id=event_id,
                            reason=f"max attempts reached: {error}", claim=claim,
                        ):
                            result["dead_lettered"] += 1
                        else:
                            result["failed"] += 1
                    except Exception:
                        result["failed"] += 1
                else:
                    await self.receipts.fail(claim, error)
                    result["failed"] += 1
                continue

            if not await self.receipts.complete(claim):
                result["failed"] += 1
                continue
            await self.transport.acknowledge(
                tenant_id=self.tenant_id, topic=self.topic, group=self.group, message_id=message_id
            )
            result["processed"] += 1
        return result
