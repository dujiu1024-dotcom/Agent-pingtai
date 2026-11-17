# Metrics and structured operational logs

C-16-02 adds a semantic telemetry contract over the existing in-memory and Prometheus metrics recorders.

## Metrics

Use `PlatformTelemetry` for stable operational measurements:

- `model_tokens_total` and `model_cost_usd_total`: provider-reported token totals and explicitly estimated/settled USD cost.
- `operation_duration_seconds` and `operations_total`: latency and success/failure outcomes; `operation_rejections_total` records denied operations.
- `operation_retries_total`: retry/fallback attempts.
- `tool_failures_total`: tool invocation failures.
- `queue_depth`: sampled backlog depth, observed by the owning queue worker.

Only bounded component, status, operation, configured model-template and queue labels are accepted. Tenant, user, Run, request, tool-call, prompt and document identifiers are never metric labels. Never use raw exception messages as labels.

## Structured logs

Use `StructuredEventLogger.emit` for one-line JSON events. The event envelope contains a UTC timestamp and bounded component/operation/status fields. Optional fields are explicitly allow-listed and recursively redacted before serialization. Prompts, model output, credentials, request payloads and arbitrary IDs are rejected.

## Failure semantics

Metrics/logging are operational side channels: callers should isolate exporter failures from business execution (the existing `safe_metric` helper is suitable). The in-memory recorder is for tests/single-process use; production deployments should use the Prometheus adapter and expose its private registry through the host's authenticated metrics endpoint. Queue depth must be sampled by the owning queue worker because only it has an authoritative backlog view.
