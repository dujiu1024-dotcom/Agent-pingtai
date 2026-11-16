"""Polling worker for durable alert notification delivery.

The worker is deliberately independent of the web process lifecycle. Deploy it
as a dedicated process or attach it to an application lifespan explicitly.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol


class AlertDispatcher(Protocol):
    async def retry_pending(self, *, limit: int = 100) -> dict[str, int]: ...


class AlertDispatchWorker:
    """Poll the durable outbox, retry failed deliveries, and stop gracefully."""

    def __init__(
        self,
        dispatcher: AlertDispatcher,
        *,
        poll_interval_seconds: float = 5.0,
        batch_size: int = 100,
    ) -> None:
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not 0.1 <= poll_interval_seconds <= 3600
        ):
            raise ValueError("poll interval must be between 0.1 and 3600 seconds")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
            raise ValueError("batch size must be between 1 and 1000")
        self.dispatcher = dispatcher
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.batch_size = batch_size
        self._stop = asyncio.Event()
        self.cycles = 0
        self.last_result: dict[str, int] = {"claimed": 0, "sent": 0, "failed": 0}
        self.last_error_type: str | None = None

    def stop(self) -> None:
        """Request graceful shutdown; safe to call from the owning event loop."""
        self._stop.set()

    async def run_once(self) -> dict[str, int]:
        result = await self.dispatcher.retry_pending(limit=self.batch_size)
        normalized = {
            key: int(result.get(key, 0))
            for key in ("claimed", "sent", "failed")
        }
        if any(value < 0 for value in normalized.values()):
            raise ValueError("dispatcher counters must be non-negative")
        self.last_result = normalized
        self.last_error_type = None
        self.cycles += 1
        return dict(normalized)

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Run until stopped; isolate a failed polling cycle and retry next interval."""
        stop = stop_event or self._stop
        while not stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep diagnostics bounded and never retain provider exception text.
                self.last_error_type = type(exc).__name__
                self.last_result = {"claimed": 0, "sent": 0, "failed": 0}
                self.cycles += 1
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue
