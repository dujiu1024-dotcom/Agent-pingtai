"""Versioned error classification, bounded retries, circuit breaking and fallbacks."""
from __future__ import annotations

import asyncio
import random
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Sequence


class ErrorKind(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    CAPABILITY = "capability"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class RuntimeFailure(RuntimeError):
    """Base error carrying an explicit, stable runtime error kind."""


class TransientRuntimeError(RuntimeFailure): kind = ErrorKind.TRANSIENT
class RateLimitRuntimeError(RuntimeFailure): kind = ErrorKind.RATE_LIMIT
class RuntimeTimeoutError(RuntimeFailure): kind = ErrorKind.TIMEOUT
class OutputValidationRuntimeError(RuntimeFailure): kind = ErrorKind.VALIDATION
class RuntimeAuthenticationError(RuntimeFailure): kind = ErrorKind.AUTHENTICATION
class RuntimeAuthorizationError(RuntimeFailure): kind = ErrorKind.AUTHORIZATION
class RuntimeNotFoundError(RuntimeFailure): kind = ErrorKind.NOT_FOUND
class CapabilityRuntimeError(RuntimeFailure): kind = ErrorKind.CAPABILITY


@dataclass(frozen=True)
class RetryRule:
    """max_attempts includes the first call; zero retries is max_attempts=1."""
    max_attempts: int
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 8.0
    retryable: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")


@dataclass(frozen=True)
class RetryPolicy:
    policy_id: str
    version: int
    rules: Mapping[ErrorKind, RetryRule] = field(default_factory=dict)
    default_rule: RetryRule = field(default_factory=lambda: RetryRule(1, retryable=False))
    jitter_ratio: float = 0.0

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or len(self.policy_id) > 128:
            raise ValueError("policy_id is required")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("policy version must be a positive integer")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")
        if any(not isinstance(k, ErrorKind) or not isinstance(v, RetryRule) for k, v in self.rules.items()):
            raise ValueError("rules must map ErrorKind values to RetryRule values")

    def rule_for(self, kind: ErrorKind) -> RetryRule:
        return self.rules.get(kind, self.default_rule)


class RetryPolicyRegistry:
    """In-process append-only registry; a policy version cannot be overwritten."""
    def __init__(self) -> None:
        self._items: dict[tuple[str, int], RetryPolicy] = {}

    def register(self, policy: RetryPolicy) -> None:
        key = (policy.policy_id, policy.version)
        existing = self._items.get(key)
        if existing is not None and existing != policy:
            raise ValueError("retry policy versions are immutable")
        self._items[key] = policy

    def resolve(self, policy_id: str, version: int) -> RetryPolicy:
        try:
            return self._items[(policy_id, version)]
        except KeyError as exc:
            raise KeyError(f"unknown retry policy {policy_id}@{version}") from exc


def classify_error(error: BaseException) -> ErrorKind:
    """Classify using explicit kind declarations, then conservative type fallbacks."""
    if isinstance(error, asyncio.CancelledError):
        return ErrorKind.PERMANENT
    declared = getattr(error, "kind", None)
    if isinstance(declared, ErrorKind):
        return declared
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return ErrorKind.TIMEOUT
    if isinstance(error, (ConnectionError, OSError)):
        return ErrorKind.TRANSIENT
    if isinstance(error, ValueError):
        return ErrorKind.VALIDATION
    return ErrorKind.UNKNOWN


class CircuitOpenError(RuntimeError):
    """Raised when the provider circuit is open and calls are rejected."""


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe breaker with one half-open probe and monotonic cooldown."""
    def __init__(self, *, failure_threshold: int = 5, reset_timeout_seconds: float = 30.0,
                 clock: Callable[[], float] | None = None) -> None:
        if failure_threshold < 1 or reset_timeout_seconds <= 0:
            raise ValueError("invalid circuit breaker configuration")
        import time
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._refresh()
            return self._state

    def _refresh(self) -> None:
        if (self._state == CircuitState.OPEN and self._opened_at is not None
                and self._clock() - self._opened_at >= self.reset_timeout_seconds):
            self._state = CircuitState.HALF_OPEN

    def before_call(self) -> None:
        with self._lock:
            self._refresh()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError("provider circuit is open")
            if self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    raise CircuitOpenError("provider circuit is awaiting its half-open probe")
                self._probe_in_flight = True

    def record_success(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._probe_in_flight = False
            self._failures += 1
            if self._state == CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()


@dataclass(frozen=True)
class FallbackChannel:
    name: str
    invoke: Callable[[], Awaitable[Any]]
    enabled: bool = True


@dataclass(frozen=True)
class ExecutionOutcome:
    value: Any
    channel: str
    attempts: int
    error_kind: ErrorKind | None = None


class RuntimeReliabilityExecutor:
    """Apply a pinned policy to a primary channel, then ordered safe fallbacks."""
    def __init__(self, *, policies: RetryPolicyRegistry,
                 breakers: Mapping[str, CircuitBreaker] | None = None,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 random_value: Callable[[], float] = random.random) -> None:
        self.policies = policies
        self.breakers = dict(breakers or {})
        self.sleep = sleep
        self.random_value = random_value

    @staticmethod
    def _delay(rule: RetryRule, retry_index: int, jitter_ratio: float,
               random_value: Callable[[], float]) -> float:
        raw = min(rule.max_delay_seconds, rule.base_delay_seconds * (2 ** retry_index))
        if not jitter_ratio or not raw:
            return raw
        sample = min(1.0, max(0.0, random_value()))
        return raw * (1 - jitter_ratio + 2 * jitter_ratio * sample)

    async def execute(self, *, primary: Callable[[], Awaitable[Any]],
                      policy_id: str, policy_version: int, breaker_key: str,
                      fallbacks: Sequence[FallbackChannel] = ()) -> ExecutionOutcome:
        policy = self.policies.resolve(policy_id, policy_version)
        breaker = self.breakers.get(breaker_key)
        last_error: BaseException | None = None
        last_kind = ErrorKind.UNKNOWN
        attempts = 0

        if breaker is None or breaker.state != CircuitState.OPEN:
            rule: RetryRule | None = None
            try:
                if breaker is not None:
                    breaker.before_call()
                while True:
                    attempts += 1
                    try:
                        value = await primary()
                        if breaker is not None:
                            breaker.record_success()
                        return ExecutionOutcome(value, "primary", attempts)
                    except asyncio.CancelledError:
                        if breaker is not None:
                            breaker.record_success()
                        raise
                    except Exception as exc:
                        last_error = exc
                        last_kind = classify_error(exc)
                        rule = rule or policy.rule_for(last_kind)
                        if not rule.retryable or attempts >= rule.max_attempts:
                            break
                        await self.sleep(self._delay(
                            rule, attempts - 1, policy.jitter_ratio, self.random_value
                        ))
                if breaker is not None:
                    breaker.record_failure()
            except CircuitOpenError as exc:
                last_error = exc
                last_kind = ErrorKind.TRANSIENT
        else:
            last_error = CircuitOpenError("provider circuit is open")
            last_kind = ErrorKind.TRANSIENT

        # Security, contract and caller-cancellation errors must never be hidden
        # by a fallback. A fallback is an explicit channel, not a retry of the same one.
        if last_kind in {ErrorKind.AUTHENTICATION, ErrorKind.AUTHORIZATION,
                         ErrorKind.VALIDATION, ErrorKind.CAPABILITY, ErrorKind.PERMANENT}:
            assert last_error is not None
            raise last_error

        for channel in fallbacks:
            if not channel.enabled:
                continue
            try:
                value = await channel.invoke()
                return ExecutionOutcome(value, channel.name, attempts, last_kind)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                last_kind = classify_error(exc)

        if last_error is not None:
            raise last_error
        raise RuntimeError("runtime execution failed without an error")
