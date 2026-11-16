"""Lifecycle-managed polling worker for durable alert notification delivery."""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
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
            raise ValueError("batch size must be an integer between 1 and 1000")
        self.dispatcher = dispatcher
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.batch_size = batch_size
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.cycles = 0
        self.last_result: dict[str, int] = {"claimed": 0, "sent": 0, "failed": 0}
        self.last_error_type: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start one background polling task; repeated starts are idempotent."""
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self.run_forever(), name="platform-alert-dispatch")

    async def stop(self) -> None:
        """Stop the polling task and wait for its current cycle to finish."""
        task = self._task
        if task is None:
            self._stop.set()
            return
        self._stop.set()
        try:
            await task
        finally:
            self._task = None

    async def run_once(self) -> dict[str, int]:
        result = await self.dispatcher.retry_pending(limit=self.batch_size)
        normalized: dict[str, int] = {}
        for key in ("claimed", "sent", "failed"):
            value = result.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("dispatcher counters must be non-negative integers")
            normalized[key] = value
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
                self.last_error_type = type(exc).__name__
                self.last_result = {"claimed": 0, "sent": 0, "failed": 0}
                self.cycles += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                continue



class AlertRuleEvaluationWorker:
    """Poll metrics, persist rule events to the durable alert outbox, then advance cooldowns."""

    def __init__(
        self,
        *,
        manager: Any,
        metrics_provider: Any,
        event_store: Any,
        scope_key: str,
        poll_interval_seconds: float = 15.0,
    ) -> None:
        if not isinstance(scope_key, str) or not scope_key.strip():
            raise ValueError("scope_key is required")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not 0.1 <= poll_interval_seconds <= 3600
        ):
            raise ValueError("poll interval must be between 0.1 and 3600 seconds")
        if not callable(getattr(manager, "evaluate_candidates", None)) or not callable(
            getattr(manager, "mark_fired", None)
        ):
            raise TypeError("manager must expose evaluate_candidates() and mark_fired()")
        if not callable(metrics_provider) or not callable(getattr(event_store, "record_once", None)):
            raise TypeError("metrics_provider and event_store.record_once are required")
        self.manager = manager
        self.metrics_provider = metrics_provider
        self.event_store = event_store
        self.scope_key = scope_key
        self.poll_interval_seconds = float(poll_interval_seconds)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.cycles = 0
        self.last_result = {"observed": 0, "fired": 0, "created": 0, "deduplicated": 0}
        self.last_error_type: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run_once(self) -> dict[str, int]:
        metrics = self.metrics_provider()
        if inspect.isawaitable(metrics):
            metrics = await metrics
        if not isinstance(metrics, Mapping):
            raise TypeError("metrics provider must return a metric dictionary")
        candidates = self.manager.evaluate_candidates(metrics)
        created = deduplicated = 0
        rules = {rule.rule_id: rule for rule in self.manager.rules}
        for event in candidates:
            rule = rules.get(event.rule_id)
            if rule is None:
                raise ValueError("alert candidate has no configured rule")
            window = max(1, int(rule.cooldown.total_seconds()))
            bucket = int(event.fired_at.timestamp()) // window
            _event_id, inserted = await self.event_store.record_once(
                event,
                scope_key=self.scope_key,
                dedupe_key=f"{event.rule_id}:{bucket}",
            )
            if inserted:
                created += 1
            else:
                deduplicated += 1
            self.manager.mark_fired((event,))
        result = {
            "observed": len(metrics), "fired": len(candidates),
            "created": created, "deduplicated": deduplicated,
        }
        self.last_result = result
        self.last_error_type = None
        self.cycles += 1
        return dict(result)

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or self._stop
        while not stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error_type = type(exc).__name__
                self.cycles += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                continue

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self.run_forever(), name="platform-alert-rule-evaluation",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            self._stop.set()
            return
        self._stop.set()
        try:
            await task
        finally:
            self._task = None
