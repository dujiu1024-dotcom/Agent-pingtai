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


class IdempotentEventEffectExecutor(Protocol):
    """Downstream must durably deduplicate the supplied key before side effects."""

    async def execute(
        self, *, idempotency_key: str, event: EventEnvelope
    ) -> Mapping[str, Any]: ...


def event_idempotency_key(event: EventEnvelope) -> str:
    if not event.tenant_id.strip() or not event.event_id.strip():
        raise ValueError("tenant_id and event_id are required for idempotent effects")
    try:
        normalized_event_id = str(UUID(event.event_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("event_id must be a UUID for idempotent effects") from exc
    return f"tenant:{event.tenant_id}:event:{normalized_event_id}"


class IdempotentEventHandler:
    """Bind every downstream side effect to the stable tenant/event identity."""

    def __init__(self, executor: IdempotentEventEffectExecutor) -> None:
        self.executor = executor

    async def __call__(self, event: EventEnvelope) -> None:
        result = await self.executor.execute(
            idempotency_key=event_idempotency_key(event),
            event=event,
        )
        if not isinstance(result, Mapping):
            raise TypeError("idempotent effect executor must return a mapping")


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


class RedisStreamsConsumerWorker:
    """Poll one tenant-scoped consumer; deployments should create one worker per tenant."""

    def __init__(
        self,
        *,
        consumer: RedisStreamsConsumer,
        interval_seconds: float = 1.0,
        batch_size: int = 20,
        block_ms: int = 1000,
        pending_idle_ms: int = 30_000,
    ) -> None:
        import asyncio

        if not 0.1 <= interval_seconds <= 300:
            raise ValueError("interval_seconds must be between 0.1 and 300")
        if not 1 <= batch_size <= 500 or block_ms < 0 or pending_idle_ms < 0:
            raise ValueError("invalid Redis Streams consumer polling configuration")
        self.consumer = consumer
        self.interval_seconds = interval_seconds
        self.batch_size = batch_size
        self.block_ms = block_ms
        self.pending_idle_ms = pending_idle_ms
        self._asyncio = asyncio
        self._stop = asyncio.Event()
        self._task = None
        self._connected = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        transport = self.consumer.transport
        connect = getattr(transport, "connect", None)
        if callable(connect):
            await connect()
            self._connected = True
        try:
            ensure_group = getattr(transport, "ensure_group", None)
            if callable(ensure_group):
                await ensure_group(
                    tenant_id=self.consumer.tenant_id,
                    topic=self.consumer.topic,
                    group=self.consumer.group,
                )
        except Exception:
            await self._close_transport()
            raise
        self._stop = self._asyncio.Event()
        self._task = self._asyncio.create_task(
            self._run(), name=f"event-consumer-{self.consumer.consumer_name}",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            await self._close_transport()
            return
        self._stop.set()
        try:
            await task
        finally:
            self._task = None
            await self._close_transport()

    async def _close_transport(self) -> None:
        if self._connected:
            close = getattr(self.consumer.transport, "close", None)
            try:
                if callable(close):
                    await close()
            finally:
                self._connected = False

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.consumer.consume_batch(
                    count=self.batch_size,
                    block_ms=self.block_ms,
                    pending_idle_ms=self.pending_idle_ms,
                )
            except self._asyncio.CancelledError:
                raise
            except Exception:
                # A transient broker or receipt-store failure must not kill polling.
                pass
            try:
                await self._asyncio.wait_for(
                    self._stop.wait(), timeout=self.interval_seconds,
                )
            except self._asyncio.TimeoutError:
                pass
