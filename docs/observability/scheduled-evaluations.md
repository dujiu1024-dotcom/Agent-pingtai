# Scheduled Agent/RAG evaluations

`EvaluationScheduleWorker` runs host-defined evaluation plans using immutable dataset versions and model keys. Each schedule carries an explicit tenant, dataset ID/version, model key, and interval. The worker bounds concurrent runs, retries failed plans on the next polling cycle, and stores only exception class names in operational state.

## Host integration

Inject a configured worker as `evaluation_worker` into `create_platform_app()`. The API composition root starts and stops it with the application lifecycle. Schedule definitions must come from trusted deployment configuration; they are not accepted from untrusted API requests.

## Deployment boundary

The scheduler is process-local and does not provide distributed leader election or durable schedule storage. In multi-replica deployments, run it in a singleton worker deployment or guard startup with a host-provided distributed lease. Evaluation execution still uses the existing `EvaluationRunService`, which pins dataset version/model key, bounds per-run case concurrency, persists immutable reports, and records failed release-gate outcomes. Production model/RAG endpoints and representative dataset content remain host-provided configuration and require environment-specific acceptance.
