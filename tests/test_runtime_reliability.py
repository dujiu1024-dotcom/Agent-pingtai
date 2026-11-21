import asyncio
import pytest

from agent_platform.runtime.reliability import (
    CircuitBreaker, CircuitOpenError, CircuitState, ErrorKind, FallbackChannel,
    RetryPolicy, RetryPolicyRegistry, RetryRule, RuntimeReliabilityExecutor,
    TransientRuntimeError, classify_error,
)


def registry():
    policies = RetryPolicyRegistry()
    policies.register(RetryPolicy(
        "default", 1,
        rules={
            ErrorKind.TRANSIENT: RetryRule(3, 0.01, 0.02),
            ErrorKind.RATE_LIMIT: RetryRule(2, 0.02, 0.02),
            ErrorKind.TIMEOUT: RetryRule(2, 0.01, 0.01),
            ErrorKind.VALIDATION: RetryRule(1, retryable=False),
            ErrorKind.AUTHORIZATION: RetryRule(1, retryable=False),
        },
        default_rule=RetryRule(1, retryable=False),
    ))
    return policies


def test_policy_versions_are_immutable():
    policies = registry()
    with pytest.raises(ValueError):
        policies.register(RetryPolicy("default", 1, default_rule=RetryRule(2)))


def test_error_classification_is_conservative():
    assert classify_error(TransientRuntimeError()) is ErrorKind.TRANSIENT
    assert classify_error(TimeoutError()) is ErrorKind.TIMEOUT
    assert classify_error(ValueError()) is ErrorKind.VALIDATION


@pytest.mark.asyncio
async def test_retries_transient_errors_with_bounded_attempts():
    waits = []
    async def sleep(delay):
        waits.append(delay)
    calls = 0
    async def primary():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TransientRuntimeError("temporary")
        return "ok"
    executor = RuntimeReliabilityExecutor(policies=registry(), sleep=sleep)
    outcome = await executor.execute(
        primary=primary, policy_id="default", policy_version=1, breaker_key="provider"
    )
    assert (outcome.value, outcome.channel, outcome.attempts) == ("ok", "primary", 3)
    assert waits == [0.01, 0.02]


@pytest.mark.asyncio
async def test_validation_errors_are_not_retried_or_hidden_by_fallback():
    calls = 0
    async def primary():
        nonlocal calls
        calls += 1
        raise ValueError("invalid output")
    async def fallback():
        pytest.fail("validation failure must not fall back")
    executor = RuntimeReliabilityExecutor(policies=registry())
    with pytest.raises(ValueError):
        await executor.execute(
            primary=primary, policy_id="default", policy_version=1,
            breaker_key="provider", fallbacks=[FallbackChannel("backup", fallback)]
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_fallback_is_used_after_primary_retries_exhausted():
    calls = 0
    async def primary():
        nonlocal calls
        calls += 1
        raise TransientRuntimeError("offline")
    async def fallback():
        return "degraded"
    executor = RuntimeReliabilityExecutor(policies=registry(), sleep=lambda _: asyncio.sleep(0))
    outcome = await executor.execute(
        primary=primary, policy_id="default", policy_version=1, breaker_key="provider",
        fallbacks=[FallbackChannel("secondary-model", fallback)]
    )
    assert calls == 3
    assert (outcome.value, outcome.channel, outcome.attempts) == ("degraded", "secondary-model", 3)
    assert outcome.error_kind is ErrorKind.TRANSIENT


def test_circuit_breaker_opens_and_allows_single_half_open_probe():
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=5, clock=lambda: now[0])
    breaker.before_call()
    breaker.record_failure()
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    now[0] = 5.0
    breaker.before_call()
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_open_circuit_skips_primary_and_uses_fallback():
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=60)
    breaker.before_call()
    breaker.record_failure()
    async def primary():
        pytest.fail("open circuit must reject primary")
    async def fallback():
        return "cached"
    executor = RuntimeReliabilityExecutor(policies=registry(), breakers={"provider": breaker})
    outcome = await executor.execute(
        primary=primary, policy_id="default", policy_version=1, breaker_key="provider",
        fallbacks=[FallbackChannel("cache", fallback)]
    )
    assert outcome.value == "cached"
    assert outcome.channel == "cache"
