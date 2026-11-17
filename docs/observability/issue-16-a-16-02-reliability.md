# Issue #16 A-16-02 — security and reliability evidence

This acceptance slice supplements A-16-01's functional contract matrix. It
focuses on tenant boundaries, authorization denial, idempotency, retry,
concurrent writers/workers, and unavailable persistence dependencies.

## New focused tests

- `tests/test_issue_16_a_16_02_reliability.py`
  - Alert, evaluation and Prompt PostgreSQL adapters propagate connection
    failures; they do not turn unavailable storage into empty history or a
    false "not found" result.
- `tests/test_issue_16_a_16_02_postgres_integration.py` (requires
  `AGENT_PLATFORM_TEST_DSN`; applies repository migrations)
  - Alert events: concurrent duplicate inserts produce one durable event ID;
    the same dedupe key remains isolated across tenant scope keys.
  - Alert outbox: concurrent workers using `FOR UPDATE SKIP LOCKED` cannot
    claim the same event; a failed delivery becomes claimable again, increments
    its attempt count, and can reach sent state.
  - Evaluation: persisted reports and human scores are tenant-filtered;
    cross-tenant report reads and score writes are denied; rationale redaction
    is verified after database round-trip.
  - Prompt rollout: PostgreSQL policy and decision reads are isolated by tenant.
  - Prompt registry: concurrent registrations serialize into unique versions;
    the immutable-version trigger rejects direct UPDATE.

## Existing authorization and retry coverage reviewed

| Surface | Existing tests | Security / reliability assertion |
| --- | --- | --- |
| Trace and telemetry | `tests/test_trace_spans.py`, `tests/test_observability_metrics_safety.py` | Sensitive values are redacted; telemetry exporter failure does not break the application path. |
| Alert APIs and delivery | `tests/test_observability_alerts.py`, `tests/test_alert_dispatch.py`, `tests/test_alert_notifications.py`, `tests/test_alert_worker.py` | Tenant-scoped alert listing and permission-gated acknowledgement; audit failure blocks provider side effect; provider errors are redacted/retryable; workers recover from poll/dispatch failures. |
| Evaluation APIs and scheduler | `tests/test_observability_evaluation_api.py`, `tests/test_evaluation_governance_api.py`, `tests/test_evaluation_worker.py`, `tests/test_evaluation_runner.py` | Tenant-filtered history, explicit evaluation authorization, separate reviewer permission, bounded concurrency, failed-run retry and no partial report persistence. |
| Prompt APIs and rollout | `tests/test_prompt_api.py`, `tests/test_prompt_management_api.py`, `tests/test_prompt_operations.py`, `tests/test_prompt_rollouts.py` | Authenticated tenant-scoped reads/writes, release gate denial, audit failure blocks mutation, sticky tenant routing, inactive variant rejection and sibling isolation. |

## Execution

```bash
python -m pytest -q \
  tests/test_issue_16_a_16_02_reliability.py \
  tests/test_issue_16_a_16_02_postgres_integration.py \
  tests/test_alert_dispatch.py tests/test_alert_notifications.py \
  tests/test_alert_worker.py tests/test_observability_alerts.py \
  tests/test_evaluation_worker.py tests/test_evaluation_runner.py \
  tests/test_evaluation_governance_api.py tests/test_observability_evaluation_api.py \
  tests/test_prompt_api.py tests/test_prompt_management_api.py \
  tests/test_prompt_operations.py tests/test_prompt_rollouts.py
```

The PostgreSQL suite is explicitly skipped unless a disposable
`AGENT_PLATFORM_TEST_DSN` is supplied. CI must provide PostgreSQL to exercise
those integration assertions. This task does not claim live notification
provider, Langfuse, Prometheus scrape, model endpoint or production multi-replica
deployment drills; those remain field acceptance.
