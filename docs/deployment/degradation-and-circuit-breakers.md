# Graceful degradation and fault isolation

The `agent_platform.resilience` package provides synchronous, standard-library
primitives. Callers own dependency adapters, metrics, logging, and fallback data.

## Core versus optional paths

Declare each path with `DegradationPolicy`:

- `FailureMode.REQUIRED`: propagate dependency errors unless the caller supplies
  an explicit fallback that preserves the core contract (for example, a
  bounded-staleness cache for a read path). Never silently replace a required
  operation with an empty or success-shaped response.
- `FailureMode.OPTIONAL`: isolate non-critical features such as recommendations,
  personalization, enrichment, or auxiliary telemetry. Supply a fallback such
  as an empty recommendation list or omit the enrichment field. Without a
  fallback, the executor raises `DependencyUnavailable`.

## Circuit breaker

A `CircuitBreaker` transitions CLOSED → OPEN after the configured consecutive
failure threshold. OPEN rejects calls without invoking the dependency. After
the recovery timeout it admits at most `half_open_max_calls` probes. A successful
probe closes and resets the breaker; a failed probe reopens it. State transitions
are protected by a lock. Use separate breaker instances per dependency and
isolation domain to prevent a failing optional service from affecting core paths.

## Bulkhead

A `Bulkhead` bounds concurrent work for one dependency. Calls that cannot
acquire a slot within `wait_timeout` fail fast with `BulkheadRejected`.
Slots are released in a `finally` block, including when operations raise.
Use independent capacities for independent downstream services.

## Example

```python
from agent_platform.resilience import (
    CircuitBreaker, DegradationPolicy, FailureMode, ResilientExecutor,
)

recommendations = ResilientExecutor(
    DegradationPolicy("recommendations", FailureMode.OPTIONAL),
    circuit_breaker=CircuitBreaker(failure_threshold=3, recovery_timeout=15),
)
items = recommendations.execute(fetch_recommendations, fallback=lambda _exc: [])
```

The primitives do not perform retries, health checks, distributed coordination,
or telemetry export. Configure bounded timeouts in dependency adapters; share
breaker state across processes only when a dedicated distributed implementation
is introduced. Avoid sharing one breaker between critical and optional paths.
