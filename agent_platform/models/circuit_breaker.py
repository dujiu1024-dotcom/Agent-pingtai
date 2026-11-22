"""Async circuit breaker for model provider failover.

Router contract (from runtime_router.py):
  await allow_request(breaker_key) -> bool
  await record_success(breaker_key)
  await record_failure(breaker_key)

Thread-safe per-key state using asyncio.Lock + monotonic clock.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    """Raised by the router when the provider circuit is open."""


@dataclass
class _BreakerState:
    failures: int = 0
    success: int = 0
    last_failure_at: float = 0.0
    opened_at: float = 0.0
    state: str = "closed"  # closed | open | half-open
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class AsyncCircuitBreaker:
    """Per-provider async circuit breaker with automatic half-open recovery.

    Parameters are provider-level defaults. Each provider's template may
    override via `circuit_breaker` dict in the template definition.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        half_open_max_calls: int = 1,
        clock=None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout_seconds
        self._half_open_max = half_open_max_calls
        self._clock = clock or time.monotonic
        self._states: dict[str, _BreakerState] = {}
        self._global_lock = asyncio.Lock()

    def _get_or_create(self, key: str) -> _BreakerState:
        state = self._states.get(key)
        if state is None:
            state = _BreakerState()
            self._states[key] = state
        return state

    async def allow_request(self, breaker_key: str) -> bool:
        """Check if a request is allowed through the breaker.

        Returns False only when the breaker is OPEN and recovery_timeout
        has NOT yet elapsed. After recovery_timeout, breaker moves to
        HALF_OPEN and allows up to `half_open_max_calls` probe calls.
        """
        state = self._get_or_create(breaker_key)
        async with state.lock:
            if state.state == "closed":
                return True
            if state.state == "open":
                elapsed = self._clock() - state.opened_at
                if elapsed >= self._recovery_timeout:
                    state.state = "half-open"
                    state.failures = 1  # first half-open probe slot consumed
                    state.success = 0
                    logger.info(
                        "circuit %s open→half-open after %.1fs", breaker_key, elapsed
                    )
                    return True
                return False
            # half-open: allow up to half_open_max concurrent probes.
            # Each call to allow_request that returns True increments the
            # probe counter so the next caller sees the budget consumed.
            probes = state.success + state.failures
            if probes < self._half_open_max:
                state.failures += 1  # consume one probe slot
                return True
            return False

    async def record_success(self, breaker_key: str) -> None:
        state = self._get_or_create(breaker_key)
        async with state.lock:
            if state.state == "half-open":
                state.success += 1
                if state.success >= self._half_open_max:
                    state.state = "closed"
                    state.failures = 0
                    state.success = 0
                    logger.info("circuit %s half-open→closed (recovered)", breaker_key)
            elif state.state == "closed":
                state.failures = 0  # reset on success

    async def record_failure(self, breaker_key: str) -> None:
        state = self._get_or_create(breaker_key)
        async with state.lock:
            state.failures += 1
            state.last_failure_at = self._clock()
            if state.state == "half-open":
                # A probe failed → back to open, reset timer
                state.state = "open"
                state.opened_at = self._clock()
                logger.warning(
                    "circuit %s half-open→open (probe failure, %d total)",
                    breaker_key, state.failures,
                )
            elif state.state == "closed" and state.failures >= self._failure_threshold:
                state.state = "open"
                state.opened_at = self._clock()
                logger.warning(
                    "circuit %s closed→open (%d failures in a row)",
                    breaker_key, state.failures,
                )

    def state(self, breaker_key: str) -> str:
        s = self._states.get(breaker_key)
        return s.state if s else "closed"

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            k: {
                "state": v.state,
                "failures": v.failures,
                "success": v.success,
                "last_failure_at": v.last_failure_at,
            }
            for k, v in self._states.items()
        }

class ModelCircuitBreaker(AsyncCircuitBreaker):
    """Backward-compatible name and timeout argument for model routing callers."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 30.0,
        half_open_max_calls: int = 1,
        clock=None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if reset_timeout_seconds <= 0:
            raise ValueError("reset_timeout_seconds must be > 0")
        super().__init__(
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=reset_timeout_seconds,
            half_open_max_calls=half_open_max_calls,
            clock=clock,
        )
