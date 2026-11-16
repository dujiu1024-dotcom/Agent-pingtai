"""Lifecycle-managed PostgreSQL Outbox relay worker for Redis recovery."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class OutboxRelayWorker:
    """Continuously republishes durable PostgreSQL events to the event transport.

    PostgreSQL remains the source of truth. Failed Redis publishes release their
    lease with backoff in PostgresOutboxStore, so the next cycle retries them.
    """

    def __init__(
        self,
        *,
        relay: Any,
        interval_seconds: float = 1.0,
        batch_limit: int = 100,
    ) -> None:
        if not 0.1 <= interval_seconds <= 300:
            raise ValueError("interval_seconds must be between 0.1 and 300")
        if not 1 <= batch_limit <= 500:
            raise ValueError("batch_limit must be between 1 and 500")
        self.relay = relay
        self.interval_seconds = interval_seconds
        self.batch_limit = batch_limit
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
            self._run(), name="platform-outbox-relay",
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
                await self.relay.publish_pending(limit=self.batch_limit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never log event payloads or exception text from external transports.
                logger.warning("outbox relay cycle failed (%s)", type(exc).__name__)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.interval_seconds,
                )
            except TimeoutError:
                pass
