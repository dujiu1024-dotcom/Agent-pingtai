"""Lifecycle-managed webhook dispatcher worker; PostgreSQL leases coordinate replicas."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class WebhookDeliveryWorker:
    def __init__(self, *, dispatcher: Any, interval_seconds: float = 2.0,
                 batch_limit: int = 50) -> None:
        if not 0.1 <= interval_seconds <= 300:
            raise ValueError("interval_seconds must be between 0.1 and 300")
        if not 1 <= batch_limit <= 500:
            raise ValueError("batch_limit must be between 1 and 500")
        self.dispatcher = dispatcher
        self.interval_seconds = interval_seconds
        self.batch_limit = batch_limit
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="platform-webhook-delivery")

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
                await self.dispatcher.dispatch(limit=self.batch_limit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Avoid logging payloads, destination URLs, credentials or exception text.
                logger.warning("webhook delivery cycle failed (%s)", type(exc).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
