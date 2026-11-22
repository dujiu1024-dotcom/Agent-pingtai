"""Consume durable ACL/content invalidations and fan them out to cache listeners.

The invalidation row and source ACL mutation are committed together by the
repository. This worker uses SKIP LOCKED for multi-replica safety and only
acknowledges an event after PostgreSQL has accepted the transactional NOTIFY.
Listeners must treat notifications as hints and re-read authoritative ACLs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from agent_platform.knowledge.acl_invalidation_events import KnowledgeACLInvalidationEvent

logger = logging.getLogger(__name__)

_CHANNEL = "knowledge_cache_invalidation"


class PostgresKnowledgeInvalidationConsumer:
    """At-least-once dispatcher for the durable knowledge invalidation table."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connect = connection_factory

    async def process_once(self, *, limit: int = 100) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT event_id, tenant_id, resource_type, resource_id,
                              reason, created_at
                       FROM knowledge_cache_invalidations
                       WHERE processed_at IS NULL
                       ORDER BY event_id
                       LIMIT %s
                       FOR UPDATE SKIP LOCKED""",
                    (limit,),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    event = KnowledgeACLInvalidationEvent.from_record(row)
                    await connection.execute(
                        "SELECT pg_notify(%s, %s)",
                        (_CHANNEL, event.to_json()),
                    )
                    await connection.execute(
                        """UPDATE knowledge_cache_invalidations
                           SET processed_at = now()
                           WHERE event_id = %s AND processed_at IS NULL""",
                        (row["event_id"],),
                    )
        return len(rows)


class KnowledgeInvalidationWorker:
    """Lifecycle-managed poller; transient DB errors never discard pending events."""

    def __init__(
        self, *, consumer: PostgresKnowledgeInvalidationConsumer,
        interval_seconds: float = 0.5, batch_size: int = 100,
    ) -> None:
        if not 0.1 <= interval_seconds <= 60:
            raise ValueError("interval_seconds must be between 0.1 and 60")
        if not 1 <= batch_size <= 500:
            raise ValueError("batch_size must be between 1 and 500")
        self.consumer = consumer
        self.interval_seconds = interval_seconds
        self.batch_size = batch_size
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(), name="knowledge-acl-invalidation-worker",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        try:
            await task
        finally:
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await self.consumer.process_once(limit=self.batch_size)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "knowledge invalidation cycle failed (%s)", type(exc).__name__,
                )
                processed = 0
            if processed:
                continue
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.interval_seconds,
                )
            except TimeoutError:
                pass
