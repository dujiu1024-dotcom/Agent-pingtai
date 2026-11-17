# Model Evaluation Comparison and Reports (C-26-03)

The comparison service consumes tenant-scoped evaluation runs already persisted by
PostgresEvaluationRepository and creates an immutable matrix for one dataset version.

## API

- POST /api/platform/v1/observability/evaluations/comparisons
  - Body: dataset_id, dataset_version, optional eval_run_ids (2–32).
  - Without explicit run IDs, the latest 100 tenant/dataset/version runs are considered;
    at least two are required.
- GET /api/platform/v1/observability/evaluations/comparisons/{report_id}
- GET /api/platform/v1/observability/evaluations/comparisons/{report_id}/export?format=html|pdf

All endpoints require evaluation manager authorization. Repository queries always filter
by authenticated tenant. A report records each model variant, gate result, available metrics,
metric deltas to the recommended variant and a deterministic recommendation.

## Recommendation and metric availability

Ranking is deterministic: gate pass first, then average score, pass rate and retrieval recall,
then cost, latency and token usage; ties resolve by model key and run ID. Only metrics actually
present in persisted evaluation run payloads are reported. Missing metrics remain null; no
cost/token/latency value is fabricated. Current EvaluationRunService stores quality metrics
by default, so runtime usage instrumentation must populate additional metrics for those columns.

## Persistence and exports

Matrices are persisted in platform_eval_variant_reports with tenant and dataset-version
foreign keys. HTML export escapes all values. PDF export uses ReportLab; install the optional
reports extra or inject a host-provided PDF renderer. PDF is generated from the persisted report.

## Host wiring

Pass a tenant-aware EvaluationComparisonService backed by PostgresEvaluationComparisonRepository
to create_platform_app as evaluation_comparison_service, and configure
evaluation_manage_authorizer. The repository must use the platform PostgreSQL connection factory.
