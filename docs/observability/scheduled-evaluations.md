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

By default the scheduler remains process-local. For multi-replica deployments, inject a `PostgresEvaluationScheduleLeaseStore` into `EvaluationScheduleWorker(lease_store=...)`. The store uses tenant-scoped PostgreSQL rows to atomically claim a schedule, persist its next eligible run time, and release failed runs for retry. Active runs renew their lease; an expired lease can be reclaimed after a worker crash. A schedule ID is immutable across dataset/version/model/interval changes: create a new ID when changing its definition. Apply migration `090_evaluation_schedule_leases.sql` before enabling the distributed lease store. Keep the scheduler's `lease_seconds` above three seconds; heartbeat renewals occur every third of that interval. The store is an at-least-once execution guard, not an exactly-once guarantee for external provider side effects; evaluation run handlers must remain retry-safe. Production model/RAG endpoints and representative business datasets remain host-provided configuration and require environment-specific acceptance. Automated tests use a deterministic shared lease-store fixture and do not require live providers or production resources.
