"""At-least-once Redis Streams consumer with PostgreSQL deduplication receipts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from agent_platform.events.receipts import EventReceiptClaim


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


class StreamTransport(Protocol):
    async def read_group(self, **kwargs: Any) -> list[tuple[str, dict[str, str]]]: ...
    async def claim_pending(self, **kwargs: Any) -> list[tuple[str, dict[str, str]]]: ...
    async def acknowledge(self, **kwargs: Any) -> int: ...


EventHandler = Callable[[EventEnvelope], Awaitable[None]]


class RedisStreamsConsumer:
    def __init__(
        self, *, transport: StreamTransport, receipts: ReceiptStore,
        tenant_id: str, topic: str, group: str, consumer_name: str, handler: EventHandler,
    ) -> None:
        for name, value in (("tenant_id", tenant_id), ("topic", topic), ("group", group), ("consumer_name", consumer_name)):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        self.transport = transport
        self.receipts = receipts
        self.tenant_id = tenant_id
        self.topic = topic
        self.group = group
        self.consumer_name = consumer_name
        self.handler = handler

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
        result = {"read": len(messages), "processed": 0, "duplicates": 0, "busy": 0, "failed": 0}
        for message_id, fields in messages:
            try:
                event_id = fields["event_id"]
                payload = json.loads(fields["payload"])
                if not isinstance(payload, dict):
                    raise ValueError("event payload must be a JSON object")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
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

            envelope = EventEnvelope(
                tenant_id=self.tenant_id, topic=self.topic, event_id=event_id,
                message_id=message_id, payload=payload,
            )
            try:
                # Handlers must use event_id as an idempotency key for external side effects.
                await self.handler(envelope)
            except Exception as exc:
                await self.receipts.fail(claim, type(exc).__name__)
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
