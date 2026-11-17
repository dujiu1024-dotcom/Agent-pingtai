"""Runtime self-monitoring for worker heartbeats and bounded health probes.

The monitor is host-driven: it owns no network clients and never exposes probe
exception messages. A host supplies lightweight probes and starts/stops the loop
from its application lifespan.
"""
from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any


@dataclass(frozen=True)
class RuntimeProbe:
    name: str
    check: Callable[[], Any]
    timeout_seconds: float = 2.0
    critical: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise ValueError("probe name must contain 1..80 characters")
        if not callable(self.check):
            raise ValueError("probe check must be callable")
        if (isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError("probe timeout must be finite and positive")
        if not isinstance(self.critical, bool):
            raise ValueError("probe critical flag must be boolean")


@dataclass(frozen=True)
class RuntimeProbeState:
    name: str
    status: str
    consecutive_failures: int
    last_success_at: str | None
    last_checked_at: str
    duration_ms: float
    error_type: str | None = None


class RuntimeSelfMonitor:
    """Periodic worker heartbeat/probe monitor with deterministic state transitions."""

    def __init__(
        self,
        *,
        probes: Mapping[str, RuntimeProbe],
        interval_seconds: float = 15.0,
        failure_threshold: int = 3,
        on_transition: Callable[[str, RuntimeProbeState], Any] | None = None,
        clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds) or interval_seconds <= 0):
            raise ValueError("interval_seconds must be finite and positive")
        if isinstance(failure_threshold, bool) or not isinstance(failure_threshold, int) or failure_threshold < 1:
            raise ValueError("failure_threshold must be a positive integer")
        if not isinstance(probes, Mapping):
            raise TypeError("probes must be a mapping")
        if any(not isinstance(probe, RuntimeProbe) for probe in probes.values()):
            raise TypeError("probes must contain RuntimeProbe values")
        if any(name != probe.name for name, probe in probes.items()):
            raise ValueError("probe mapping keys must match probe names")
        if on_transition is not None and not callable(on_transition):
            raise TypeError("on_transition must be callable")
        self._probes = dict(probes)
        self.interval_seconds = float(interval_seconds)
        self.failure_threshold = failure_threshold
        self.on_transition = on_transition
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._states: dict[str, RuntimeProbeState] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._run_lock = asyncio.Lock()

    @property
    def states(self) -> Mapping[str, RuntimeProbeState]:
        return dict(self._states)

    @property
    def healthy(self) -> bool:
        return all(
            state.status == "ok" or not self._probes[name].critical
            for name, state in self._states.items()
        ) and len(self._states) == len(self._probes)

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.healthy else "degraded",
            "observed_at": self._wall_clock().astimezone(timezone.utc).isoformat(),
            "probes": {
                name: {
                    "status": state.status,
                    "consecutive_failures": state.consecutive_failures,
                    "last_success_at": state.last_success_at,
                    "last_checked_at": state.last_checked_at,
                    "duration_ms": state.duration_ms,
                    **({"error_type": state.error_type} if state.error_type else {}),
                }
                for name, state in sorted(self._states.items())
            },
        }

    async def check_once(self) -> Mapping[str, RuntimeProbeState]:
        async with self._run_lock:
            for name, probe in self._probes.items():
                started = self._clock()
                checked_at = self._wall_clock()
                error_type = None
                try:
                    # Run sync probes off-loop, then also support factories returning awaitables.
                    value = await asyncio.wait_for(
                        asyncio.to_thread(probe.check), timeout=probe.timeout_seconds,
                    )
                    if inspect.isawaitable(value):
                        value = await asyncio.wait_for(value, timeout=probe.timeout_seconds)
                    if value is False:
                        raise RuntimeError("probe returned false")
                    failures = 0
                    status = "ok"
                    last_success = checked_at.astimezone(timezone.utc).isoformat()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    previous = self._states.get(name)
                    failures = (previous.consecutive_failures if previous else 0) + 1
                    status = "critical" if failures >= self.failure_threshold else "warning"
                    error_type = type(exc).__name__
                    last_success = previous.last_success_at if previous else None
                state = RuntimeProbeState(
                    name=name, status=status, consecutive_failures=failures,
                    last_success_at=last_success,
                    last_checked_at=checked_at.astimezone(timezone.utc).isoformat(),
                    duration_ms=max(0.0, (self._clock() - started) * 1000),
                    error_type=error_type,
                )
                previous_status = self._states.get(name)
                self._states[name] = state
                if previous_status is None or previous_status.status != state.status:
                    await self._notify(name, state)
            return dict(self._states)

    async def _notify(self, name: str, state: RuntimeProbeState) -> None:
        if self.on_transition is None:
            return
        try:
            result = self.on_transition(name, state)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Alert delivery is a separate subsystem; telemetry must not kill monitoring.
            return

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="runtime-self-monitor")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue
