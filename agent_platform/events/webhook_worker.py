"""Lifecycle-managed webhook dispatcher worker; PostgreSQL leases coordinate replicas."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_production_webhook_delivery_worker(
    *,
    store: Any,
    resolve_secret: Any,
    interval_seconds: float = 2.0,
    batch_limit: int = 50,
    max_attempts: int = 8,
    timeout_seconds: float = 10.0,
) -> "WebhookDeliveryWorker":
    """Build the production dispatcher with DNS policy and pinned HTTPS transport.

    The caller must provide a durable tenant-scoped store and a Secret Manager
    resolver. No generic HTTP client or environment-proxy transport is used.
    """
    from agent_platform.events.webhook_http import PinnedWebhookHttpTransport
    from agent_platform.events.webhooks import WebhookDispatcher, WebhookEgressPolicy

    dispatcher = WebhookDispatcher(
        store=store,
        transport=PinnedWebhookHttpTransport(),
        resolve_secret=resolve_secret,
        egress_policy=WebhookEgressPolicy(),
        max_attempts=max_attempts,
        timeout_seconds=timeout_seconds,
    )
    return WebhookDeliveryWorker(
        dispatcher=dispatcher,
        interval_seconds=interval_seconds,
        batch_limit=batch_limit,
    )


class WebhookDeliveryWorker:
    def __init__(self, *, dispatcher: Any, interval_seconds: float = 2.0,
                 batch_limit: int = 50) -> None:
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not 0.1 <= interval_seconds <= 300
        ):
            raise ValueError("interval_seconds must be between 0.1 and 300")
        if (
            isinstance(batch_limit, bool)
            or not isinstance(batch_limit, int)
            or not 1 <= batch_limit <= 500
        ):
            raise ValueError("batch_limit must be an integer between 1 and 500")
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
