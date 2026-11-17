"""Dependency isolation, circuit breaking, and explicit degradation policies.

All primitives are synchronous and standard-library-only. Applications inject
transport/storage-specific operations and fallback behavior at the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(RuntimeError):
    """Raised when a dependency is isolated by an open circuit."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(0.0, retry_after)
        super().__init__(f"circuit is open; retry after {self.retry_after:.3f}s")


class DependencyUnavailable(RuntimeError):
    """Raised when a required dependency cannot serve a request."""


class BulkheadRejected(RuntimeError):
    """Raised when a dependency's concurrency budget is exhausted."""


class FailureMode(str, Enum):
    """Whether a failed path may be replaced with a degraded response."""

    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class DegradationPolicy:
    """Declares the service contract for one execution path."""

    name: str
    mode: FailureMode = FailureMode.REQUIRED
    fallback_reason: str = "dependency_unavailable"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("policy name must not be empty")
        if not self.fallback_reason.strip():
            raise ValueError("fallback_reason must not be empty")


class CircuitBreaker:
    """Thread-safe circuit breaker with a bounded half-open probe window."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")
        if half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be >= 1")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._clock = clock
        self._lock = Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = 0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._refresh_locked()
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures

    def _refresh_locked(self) -> None:
        if (
            self._state is CircuitState.OPEN
            and self._opened_at is not None
            and self._clock() - self._opened_at >= self.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_in_flight = 0

    def _acquire_permission(self) -> bool:
        with self._lock:
            self._refresh_locked()
            if self._state is CircuitState.OPEN:
                assert self._opened_at is not None
                raise CircuitBreakerOpen(
                    self.recovery_timeout - (self._clock() - self._opened_at)
                )
            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_in_flight >= self.half_open_max_calls:
                    raise CircuitBreakerOpen(self.recovery_timeout)
                self._half_open_in_flight += 1
                return True
            return False

    def _record_success(self, half_open_probe: bool) -> None:
        with self._lock:
            if half_open_probe and self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failures = 0
                self._opened_at = None
                self._half_open_in_flight = 0
            elif self._state is CircuitState.CLOSED:
                self._failures = 0

    def _record_failure(self, half_open_probe: bool) -> None:
        with self._lock:
            if half_open_probe or self._state is CircuitState.HALF_OPEN:
                self._open_locked()
                return
            if self._state is CircuitState.CLOSED:
                self._failures += 1
                if self._failures >= self.failure_threshold:
                    self._open_locked()

    def _open_locked(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._half_open_in_flight = 0

    def call(self, operation: Callable[[], T]) -> T:
        """Run an operation if admitted; failures count toward opening the circuit."""
        probe = self._acquire_permission()
        try:
            result = operation()
        except Exception:
            self._record_failure(probe)
            raise
        self._record_success(probe)
        return result


class Bulkhead:
    """Limits concurrent calls to a dependency without sharing its failure budget."""

    def __init__(self, capacity: int, wait_timeout: float = 0.0) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if wait_timeout < 0:
            raise ValueError("wait_timeout must be >= 0")
        self.capacity = capacity
        self.wait_timeout = wait_timeout
        self._semaphore = BoundedSemaphore(capacity)

    def call(self, operation: Callable[[], T]) -> T:
        if not self._semaphore.acquire(timeout=self.wait_timeout):
            raise BulkheadRejected("dependency concurrency limit reached")
        try:
            return operation()
        finally:
            self._semaphore.release()


class ResilientExecutor(Generic[T]):
    """Applies isolation and explicit fallback rules around one dependency path.

    Optional paths may return a fallback when the dependency is unavailable.
    Required paths never silently degrade: absent an explicit fallback they
    raise DependencyUnavailable, preserving the caller's core-function contract.
    """

    def __init__(
        self,
        policy: DegradationPolicy,
        circuit_breaker: CircuitBreaker | None = None,
        bulkhead: Bulkhead | None = None,
    ) -> None:
        self.policy = policy
        self.circuit_breaker = circuit_breaker
        self.bulkhead = bulkhead

    def execute(
        self,
        operation: Callable[[], T],
        fallback: Callable[[Exception], T] | None = None,
    ) -> T:
        def isolated() -> T:
            return self.bulkhead.call(operation) if self.bulkhead else operation()

        try:
            if self.circuit_breaker:
                return self.circuit_breaker.call(isolated)
            return isolated()
        except Exception as exc:
            if fallback is not None:
                return fallback(exc)
            if self.policy.mode is FailureMode.OPTIONAL:
                raise DependencyUnavailable(
                    f"optional path {self.policy.name!r} unavailable: "
                    f"{self.policy.fallback_reason}"
                ) from exc
            raise
