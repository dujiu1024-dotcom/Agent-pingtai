# Health checks and readiness probes

The `agent_platform.health` package provides provider-neutral health aggregation.

## Probe semantics

- **Liveness** answers whether the process can respond. It deliberately does not
  fail when a dependency is unavailable, avoiding restart loops during dependency outages.
- **Readiness** requires every critical dependency to pass. A failed optional check
  produces `degraded` but does not remove the instance from service.
- Each result records check time and duration. Failure details expose exception
  class only; exception messages, SQL, URLs, credentials and response bodies are
  intentionally excluded.

## Dependencies

- Register a database probe with a driver-specific connection factory. The adapter
  opens a short-lived connection, executes `SELECT 1`, and closes it in a finally block.
- Register external API probes against lightweight HTTP(S) health endpoints with a
  bounded timeout and explicit accepted status codes.
- Add application-specific dependencies using injected callables. Mark nonessential
  integrations as noncritical.

## Service integration

Create a registry during application startup with `build_health_registry`, then
call `registry.run()` from the health/readiness HTTP handlers. Return
`report.as_dict()`; map readiness to HTTP 200 when ready and HTTP 503 otherwise.
Expose liveness independently and do not couple it to database or third-party health.

Probe execution is synchronous; production handlers should run it in a bounded
worker or cache short-lived results to avoid blocking the event loop. Use separate
timeouts and connection-pool limits in database/client adapters. Keep health routes
unauthenticated only when network policy makes them private or when the response
contains no sensitive operational data.
