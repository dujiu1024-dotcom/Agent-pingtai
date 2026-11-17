"""Per-tenant/model circuit breaker for cost and provider protection."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Callable


class CircuitOpenError(RuntimeError):
    """A provider/model route is temporarily blocked after repeated failures."""


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False


class ModelCircuitBreaker:
    """Thread-safe-by-asyncio breaker with a single half-open probe per route."""

    def __init__(
        self, *, failure_threshold: int = 5, reset_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if isinstance(failure_threshold, bool) or not isinstance(failure_threshold, int) or failure_threshold < 1:
            raise ValueError("failure_threshold must be a positive integer")
        if reset_timeout_seconds <= 0:
            raise ValueError("reset_timeout_seconds must be positive")
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = float(reset_timeout_seconds)
        self._clock = clock
        self._states: dict[str, _CircuitState] = {}
        self._lock = asyncio.Lock()

    async def allow_request(self, route_key: str) -> bool:
        if not isinstance(route_key, str) or not route_key.strip():
            raise ValueError("route_key must be non-empty")
        async with self._lock:
            state = self._states.setdefault(route_key, _CircuitState())
            if state.opened_at is None:
                return True
            if self._clock() - state.opened_at < self.reset_timeout_seconds:
                return False
            if state.probe_in_flight:
                return False
            state.probe_in_flight = True
            return True

    async def record_failure(self, route_key: str) -> None:
        async with self._lock:
            state = self._states.setdefault(route_key, _CircuitState())
            state.failures += 1
            state.probe_in_flight = False
            if state.failures >= self.failure_threshold:
                state.opened_at = self._clock()

    async def record_success(self, route_key: str) -> None:
        async with self._lock:
            self._states[route_key] = _CircuitState()

    async def snapshot(self, route_key: str) -> dict[str, int | float | bool | None]:
        async with self._lock:
            state = self._states.get(route_key, _CircuitState())
            return {
                "failures": state.failures,
                "opened_at": state.opened_at,
                "probe_in_flight": state.probe_in_flight,
                "open": state.opened_at is not None,
            }
