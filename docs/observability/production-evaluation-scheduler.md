# Issue #16 — production evaluation scheduler

The alert notification dispatcher is already a separate process enabled by the
Compose alerts profile. This evaluation scheduler is also isolated from API
request workers and uses PostgreSQL-backed schedule leases to prevent two
replicas executing the same due schedule concurrently.

Install a trusted evaluator plugin in the immutable platform image under the
agent_platform.evaluation_case_runner entry-point group. Configure its name
with EVALUATION_RUNNER_ENTRYPOINT. The plugin object must implement async
run_case(case, tenant_id=..., model_key=...) and return EvaluationRunService's
EvalOutput. The plugin owns real model/RAG endpoint integration and secret
access; credentials do not belong in schedule JSON.

Set EVALUATION_SCHEDULES_JSON to a non-empty JSON array. Each entry pins
schedule_id, tenant_id, dataset_id, dataset_version, model_key and
interval_seconds. Changing a pinned dataset/model/version requires a new
schedule ID. Startup validates schedule shape, thresholds, concurrency and
lease bounds.

Enable with docker compose --env-file deploy/.env.production --profile evaluation
-f deploy/production.compose.yaml up -d platform-evaluation-scheduler. Do not
enable until the evaluator plugin, dataset and model endpoint are configured.

Human scores remain in the existing authenticated API/PostgreSQL repository:
tenant-scoped, reviewer-permission gated, range-validated and rationale-redacted.
The scheduler does not generate human scores. Live model/provider execution
and production notification delivery remain deployment acceptance.
