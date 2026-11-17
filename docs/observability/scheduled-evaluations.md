# Scheduled Agent/RAG evaluations

`EvaluationScheduleWorker` runs host-defined evaluation plans using immutable dataset versions and model keys. Each schedule carries an explicit tenant, dataset ID/version, model key, and interval. The worker bounds concurrent scheduled runs; `EvaluationRunService` separately bounds per-case model/RAG calls. Failed schedules are retried on the next polling cycle, while successful schedules observe their configured interval.

## Reproducibility and endpoint selection

- Dataset ID and positive integer version are pinned in each immutable schedule; a missing version fails closed instead of silently selecting latest.
- `model_key` is a stable selector resolved by the injected host `EvaluationCaseRunner` / model registry to its configured model endpoint. Endpoint URLs and credentials remain deployment configuration and are never accepted from schedule API input or persisted in evaluation reports.
- The runner receives the same tenant and model key for every case in a run. The persisted report records dataset ID/version and model key so results can be compared without recording endpoint secrets.
- The deterministic evaluator reports answer pass rate and answer score (expected/forbidden term checks), retrieval Recall@K and reciprocal rank when expected document IDs are present. Citation coverage/precision/recall are calculated by the existing knowledge RAG evaluation path; this scheduler delegates execution to that existing service rather than reimplementing metrics.

## Host integration

Inject a configured worker as `evaluation_worker` into `create_platform_app()`. The API composition root starts and stops it with the application lifecycle. Schedule definitions must come from trusted deployment configuration; they are not accepted from untrusted API requests. Configure `max_concurrent_runs` for the scheduler and `concurrency` on `EvaluationRunService` to bound both whole-run and per-case fan-out.

## Deployment boundary

The scheduler is process-local and does not provide distributed leader election or durable schedule storage. In multi-replica deployments, run it in a singleton worker deployment or guard startup with a host-provided distributed lease. Production model/RAG endpoints and representative business datasets remain host-provided configuration and require environment-specific acceptance. No live provider, production database, server, or external resource is required for automated tests.
