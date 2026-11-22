"""Per-subscriber durable checkpoints and reconnect replay for Knowledge ACL caches.

NOTIFY is only a wake-up hint. Each subscriber owns an independent offset and
must apply events idempotently. A reconnect performs a full snapshot rebuild
at a captured high-water mark before advancing its offset.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence


class CheckpointStore(Protocol):
    async def load(self, subscriber_id: str) -> int | None: ...
    async def advance(self, subscriber_id: str, event_id: int) -> None: ...


class InvalidationSource(Protocol):
    async def high_watermark(self) -> int: ...
    async def list_after(self, event_id: int, *, limit: int) -> Sequence[Mapping[str, Any]]: ...


class MemoryCheckpointStore:
    """Deterministic store for unit tests and single-process ephemeral deployments."""

    def __init__(self) -> None:
        self.offsets: dict[str, int] = {}

    async def load(self, subscriber_id: str) -> int | None:
        return self.offsets.get(_subscriber_id(subscriber_id))

    async def advance(self, subscriber_id: str, event_id: int) -> None:
        key = _subscriber_id(subscriber_id)
        _event_id(event_id)
        self.offsets[key] = max(self.offsets.get(key, 0), event_id)


class FileCheckpointStore:
    """Atomic file-backed offsets; use a shared durable volume for process restarts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def load(self, subscriber_id: str) -> int | None:
        key = _subscriber_id(subscriber_id)
        data = self._read()
        value = data.get(key)
        return None if value is None else _event_id(value)

    async def advance(self, subscriber_id: str, event_id: int) -> None:
        key = _subscriber_id(subscriber_id)
        value = _event_id(event_id)
        data = self._read()
        data[key] = max(int(data.get(key, 0)), value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=str(self.path.parent), text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read(self) -> dict[str, int]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("checkpoint file must contain a JSON object")
        result: dict[str, int] = {}
        for key, value in raw.items():
            result[_subscriber_id(key)] = _event_id(value)
        return result


class PostgresInvalidationSource:
    """Read durable invalidation events without relying on PostgreSQL NOTIFY."""

    def __init__(self, connection_factory: Callable[[], Awaitable[Any]]) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connect = connection_factory

    async def high_watermark(self) -> int:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT COALESCE(MAX(event_id), 0) AS high_watermark "
                "FROM knowledge_cache_invalidations"
            )
            row = await cursor.fetchone()
        return _event_id(row["high_watermark"])

    async def list_after(
        self, event_id: int, *, limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        offset = _event_id(event_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT event_id, tenant_id, resource_type, resource_id,
                          reason, created_at
                   FROM knowledge_cache_invalidations
                   WHERE event_id > %s
                   ORDER BY event_id
                   LIMIT %s""",
                (offset, limit),
            )
            rows = await cursor.fetchall()
        return [
            {
                "event_id": _event_id(row["event_id"]),
                "tenant_id": row["tenant_id"],
                "resource_type": row["resource_type"],
                "resource_id": str(row["resource_id"]),
                "reason": row["reason"],
                "created_at": (
                    row["created_at"].isoformat()
                    if row["created_at"] is not None else None
                ),
            }
            for row in rows
        ]


class PostgresCheckpointStore:
    """PostgreSQL-backed independent consumer offsets; no DB needed by unit tests."""

    def __init__(self, connection_factory: Callable[[], Awaitable[Any]]) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connect = connection_factory

    async def ensure_schema(self) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS knowledge_acl_consumer_checkpoints (
                       subscriber_id text PRIMARY KEY,
                       last_event_id bigint NOT NULL DEFAULT 0 CHECK (last_event_id >= 0),
                       updated_at timestamptz NOT NULL DEFAULT now()
                   )"""
            )

    async def load(self, subscriber_id: str) -> int | None:
        key = _subscriber_id(subscriber_id)
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT last_event_id
                   FROM knowledge_acl_consumer_checkpoints
                   WHERE subscriber_id = %s""",
                (key,),
            )
            row = await cursor.fetchone()
        return None if row is None else _event_id(row["last_event_id"])

    async def advance(self, subscriber_id: str, event_id: int) -> None:
        key = _subscriber_id(subscriber_id)
        value = _event_id(event_id)
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO knowledge_acl_consumer_checkpoints
                       (subscriber_id, last_event_id, updated_at)
                   VALUES (%s, %s, now())
                   ON CONFLICT (subscriber_id) DO UPDATE
                   SET last_event_id = GREATEST(
                           knowledge_acl_consumer_checkpoints.last_event_id,
                           EXCLUDED.last_event_id
                       ),
                       updated_at = now()""",
                (key, value),
            )


class KnowledgeInvalidationReplay:
    """Replay events for one cache subscriber with crash-safe offset advancement.

    The rebuild callback must replace the subscriber's complete ACL-derived cache
    state from authoritative storage. The apply callback must be idempotent:
    a crash after apply but before checkpoint advancement intentionally redelivers.
    """

    def __init__(
        self,
        *,
        subscriber_id: str,
        source: InvalidationSource,
        checkpoints: CheckpointStore,
        apply: Callable[[Mapping[str, Any]], Awaitable[None]],
        rebuild: Callable[[int], Awaitable[None]],
        batch_size: int = 200,
    ) -> None:
        self.subscriber_id = _subscriber_id(subscriber_id)
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        self.source = source
        self.checkpoints = checkpoints
        self.apply = apply
        self.rebuild = rebuild
        self.batch_size = batch_size

    async def reconnect(self, *, force_full: bool = False) -> int:
        offset = await self.checkpoints.load(self.subscriber_id)
        if force_full or offset is None:
            high_watermark = _event_id(await self.source.high_watermark())
            await self.rebuild(high_watermark)
            await self.checkpoints.advance(self.subscriber_id, high_watermark)
            offset = high_watermark
        return await self._replay_after(offset)

    async def _replay_after(self, offset: int) -> int:
        applied = 0
        while True:
            rows = await self.source.list_after(offset, limit=self.batch_size)
            if not rows:
                return applied
            ordered = sorted(rows, key=lambda row: _event_id(row["event_id"]))
            progressed = False
            for event in ordered:
                event_id = _event_id(event["event_id"])
                if event_id <= offset:
                    continue
                await self.apply(event)
                await self.checkpoints.advance(self.subscriber_id, event_id)
                offset = event_id
                applied += 1
                progressed = True
            if not progressed or len(rows) < self.batch_size:
                return applied


def _subscriber_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise ValueError("subscriber_id must be a non-empty string of at most 255 characters")
    return value.strip()


def _event_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("event_id must be a non-negative integer")
    return value
