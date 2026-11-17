# Runtime acceptance: cancellation, timeout, recovery and structured output

This acceptance slice exercises the existing Runtime lifecycle and Harness output
contract together. It does not claim a production-provider or multi-worker
deployment test.

## Required observable outcomes

| Scenario | Trigger | Required persisted/returned outcome |
| --- | --- | --- |
| Explicit cancellation | Cancel a running tenant/Run-bound operation | The operation and registered tool calls are cancelled; the Run advances through `cancelling` to terminal `cancelled`. |
| Deadline expiry | Run exceeds its positive timeout | The operation and registered tool calls are cancelled; the Run becomes terminal `timed_out` with a bounded diagnostic. |
| Worker interruption | Worker stops after a durable checkpoint | Recovery loads the checkpoint, reclaims the Run and invokes resume; the checkpoint reference remains attached. |
| Invalid structured output | JSON parse/schema validation fails | Repair attempts do not exceed the configured 0–3 retry budget; exhaustion raises `StructuredOutputValidationError` with attempts and validation errors. |

## Implementation notes

`RunLifecycleManager.run_with_timeout` now finalizes the durable Run state after
its operation has been cancelled. Explicit cancellation can no longer leave the
Run permanently in `cancelling`; timeout finalization also accepts the
`cancelling` state so a cancellation/deadline race cannot strand the Run.
Tool calls are cancelled before the terminal state is published.

Structured output remains fail-closed: a schema must be valid, versioned
references resolve the exact tenant/schema/version, and a failed bounded repair
budget is reported as a typed terminal validation error. It is not silently
returned as successful output.

## Automated evidence

`tests/test_runtime_acceptance.py` covers all four outcomes using the
in-memory lifecycle store and deterministic Harness adapter. Existing
PostgreSQL integration coverage remains responsible for durable cross-worker
leases/checkpoints; this focused test does not substitute for that integration
or production runtime evidence.

## Explicit non-claims

- No live model provider, production multi-worker deployment, or external
  service was invoked.
- In-memory tests do not prove PostgreSQL failover, lease fencing, or process
  termination semantics.
- Cancelling an asyncio task is cooperative; adapters must propagate
  cancellation to their underlying transport/process and report the capability
  honestly.
