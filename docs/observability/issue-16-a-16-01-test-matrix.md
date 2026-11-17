# Issue #16 A-16-01 — functional and contract test matrix

This matrix maps each Issue #16 encoding task to its executable unit/API/contract
coverage. The listed tests are the acceptance evidence for normal, boundary,
and failure behavior; this is not a claim that external production providers
have been exercised.

| Encoding task | Contract surface | Normal path | Boundary / negative path | Failure path |
| --- | --- | --- | --- | --- |
| C-16-01 Trace/Span correlation and redaction | `tests/test_trace_context.py`, `tests/test_trace_middleware.py`, `tests/test_trace_span_correlation.py`, `tests/test_trace_spans.py`, `tests/test_issue_16_a_16_01_contract_matrix.py` | W3C header round-trip, request → Harness/model observation, nested request/prompt/tool/retrieval/model spans and tenant/correlation propagation | malformed/zero trace IDs, unsupported component, invalid identifiers, async context restoration | application exception marks span error; exporter failure is isolated; sensitive attributes are redacted |
| C-16-02 Metrics and structured logs | `tests/test_observability.py`, `tests/test_observability_metrics_safety.py`, `tests/test_observability_alerts.py` | Harness telemetry, Prometheus counters/histograms and metrics endpoint | low-cardinality label allowlist, escaped labels, invalid metric names, negative/non-finite values | telemetry backend failure does not break Harness; Harness failures are recorded and re-raised |
| C-16-03 Alert rules and notification operations | `tests/test_observability_alerts.py`, `tests/test_alert_policy.py`, `tests/test_alert_dispatch.py`, `tests/test_alert_store.py`, `tests/test_alert_worker.py` | rule firing, event persistence, outbox delivery, acknowledgement/resolution, worker lifecycle | cooldown/rearm, inhibition thresholds, aggregation windows/caps, retry bounds, duplicate IDs and idempotency | audit/store/notification failures remain retryable; polling and dispatch worker errors are recovered without leaking error text |
| C-16-04 Agent/RAG datasets and evaluation runs | `tests/test_agent_rag_evaluation.py`, `tests/test_evaluation_datasets.py`, `tests/test_evaluation_runner.py`, `tests/test_evaluation_store.py`, `tests/test_observability_evaluation_api.py`, `tests/test_rag_metrics_api.py` | pinned dataset execution, persisted gate report, API evaluation and RAG metric response | immutable dataset fingerprint, dataset version and endpoint identity, concurrency limits, import shape/size limits | evaluator/runner failure does not persist partial reports; unknown dataset versions and unauthorized API calls fail closed |
| C-16-05 Human review, regression comparison and release gate | `tests/test_evaluation_governance.py`, `tests/test_evaluation_governance_api.py`, `tests/test_evaluation_comparison_api.py`, `tests/test_evaluation_store.py` | score persistence, comparison report creation/read/export, threshold decision | reviewer identity masking, separate reviewer permission, metric direction and evidence references | missing metrics/evidence and ambiguous threshold policy block the gate; unauthorized scoring is denied |
| C-16-06 Prompt registry and operations | `tests/test_prompt_store.py`, `tests/test_prompt_api.py`, `tests/test_prompt_variants.py`, `tests/test_prompt_experiments.py`, `tests/test_prompt_operations.py` | immutable versions, render, activation, canary, rollback and audit history | required/unknown variables, unknown versions, canary allocation and tenant-scoped identifiers | missing/rejected evaluation gate blocks activation; unauthorized writes are denied |

## Added integrated contract

`test_request_prompt_tool_retrieval_model_spans_share_trace_and_parentage`
joins the previously separate request-context, Harness/model and component-span
tests. It asserts a single W3C trace identity and parent chain across request,
Prompt, tool, retrieval and model spans, verifies tenant and component-specific
dimensions, and checks that a credential-like Prompt attribute is redacted
before export.

## Running the acceptance suite

Run the listed tests with the repository's normal test environment, for example:

```bash
python -m pytest -q \
  tests/test_trace_context.py tests/test_trace_middleware.py \
  tests/test_trace_span_correlation.py tests/test_trace_spans.py \
  tests/test_issue_16_a_16_01_contract_matrix.py \
  tests/test_observability.py tests/test_observability_metrics_safety.py \
  tests/test_observability_alerts.py tests/test_alert_policy.py \
  tests/test_alert_dispatch.py tests/test_alert_store.py tests/test_alert_worker.py \
  tests/test_agent_rag_evaluation.py tests/test_evaluation_datasets.py \
  tests/test_evaluation_runner.py tests/test_evaluation_store.py \
  tests/test_observability_evaluation_api.py tests/test_rag_metrics_api.py \
  tests/test_evaluation_governance.py tests/test_evaluation_governance_api.py \
  tests/test_evaluation_comparison_api.py \
  tests/test_prompt_store.py tests/test_prompt_api.py \
  tests/test_prompt_variants.py tests/test_prompt_experiments.py \
  tests/test_prompt_operations.py
```

PostgreSQL/Redis integration tests require their normal CI services and are
reported separately. Live Langfuse, Prometheus scrape, notification provider,
model endpoint and production release-pipeline drills remain deployment
acceptance, not mocked unit/API contract claims.
