# Issue #16 A-16-03 — delivery and runtime acceptance

**Scope:** release-readiness evidence for observability, alert operations, Agent/RAG evaluation and Prompt operations. This document distinguishes repository/CI evidence from evidence that can only be produced by a real deployment.

## 1. Repository delivery inventory

| Area | Runtime / API | Operator documentation | PostgreSQL schema |
|---|---|---|---|
| Cross-component tracing | `agent_platform/observability/trace_context.py`, `trace_spans.py`, `trace_middleware.py`, `langfuse.py` | `docs/observability/cross-component-tracing.md` | No dedicated table required for trace context; external Langfuse persistence is optional |
| Metrics and structured logs | `telemetry.py`, `prometheus.py`, `redaction.py` | `docs/observability/metrics-and-structured-logs.md` | No dedicated table required |
| Alert rules and notification operations | `alerts.py`, `alert_store.py`, `alert_dispatch.py`, `alert_notifications.py`, `alert_worker.py` | `docs/observability/alert-operations.md` | `020_alert_events.sql`, `021_alert_delivery_outbox.sql`, `076_alert_operations_reliability.sql`, `077_outbox_delivery_dead_letter.sql` |
| Evaluation datasets, runs and governance | `agent_rag_evaluation.py`, `evaluation_runner.py`, `evaluation_store.py`, `evaluation_governance.py`, `evaluation_worker.py` | `docs/observability/agent-rag-evaluation.md`, `docs/observability/evaluation-governance.md` | `019_eval_datasets.sql`, `034_eval_human_scores.sql`, `070_eval_governance_evidence.sql`, `093_eval_dataset_tenant_versions.sql`, `094_eval_variant_reports.sql`, `094_eval_variant_runs.sql` |
| Prompt registry, variants and rollout | `prompts.py`, `prompt_store.py`, `prompt_rollouts.py`, `prompt_experiments.py` | `docs/observability/prompt-operations.md`, `docs/observability/prompt-rollouts.md`, `docs/observability/prompt-variants.md` | `017_prompt_registry.sql`, `079_prompt_audit_events.sql`, `083_prompt_variants.sql`, `084_prompt_rollouts.sql` |

The schema files are additive migrations in `agent_platform/storage/postgres/`. Apply them using `scripts/migrate_postgres.sh`; run `scripts/check_migrations.sh` against the target database before rollout to compare applied migration checksums. Do not edit an already-applied migration. Take a verified backup and follow the expand/contract and rollback policy in `docs/deployment/database-migration-orchestration.md`.

## 2. Configuration contract

### Required platform process configuration

The production entrypoint is `agent_platform.production:app`; it calls `load_production_settings()` and fails import/startup when required settings are absent or invalid.

- PostgreSQL: `AGENT_PLATFORM_DSN` or `DATABASE_URL`; alternatively `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER` plus exactly one of `POSTGRES_PASSWORD` / `POSTGRES_PASSWORD_FILE`.
- Redis: `REDIS_URL`; alternatively `REDIS_HOST` plus exactly one of `REDIS_PASSWORD` / `REDIS_PASSWORD_FILE`.
- Browser origin allowlist: `ALLOWED_ORIGINS`, exact HTTPS origins only.
- Production Compose values and secret-file mounts: `deploy/.env.production.example` and `deploy/production.compose.yaml`. Copy to a protected, untracked `deploy/.env.production`; never commit secrets.

### Optional observability integrations

These are deliberately injected adapters, not silently enabled defaults:

- Langfuse: `requirements-platform.txt` pins `langfuse>=3,<4`. Configure the SDK's `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` through the deployment secret manager. The host must inject `LangfuseObservationSink`; verify the SDK client and outbound allowlist before enabling it.
- Prometheus: `requirements-platform.txt` pins `prometheus-client>=0.21,<1`. Inject `PrometheusClientMetricsRecorder` and expose its private registry only on an authenticated/isolated scrape endpoint. Do not use tenant, user, Run, request or prompt IDs as labels.
- Alert notification: inject `WebhookAlertNotificationProvider` with an HTTPS endpoint, explicit bare-host allowlist, bounded timeout and secret-backed authorization header. Inject the durable PostgreSQL store, audit sink and `AlertDispatchWorker`; configure exactly one alert rule evaluator per tenant scope or use an external lease/leader election.
- Evaluation: pin dataset ID/version, endpoint ID, model key/version and concurrency in trusted host configuration. Endpoint credentials belong in the secret manager, never in plans or reports. `EvaluationScheduleWorker` is process-local; production multi-replica scheduling requires an external distributed lease or a single elected scheduler.
- Prompt operations: the host must inject the PostgreSQL prompt registry, authorization callbacks, audit writer and server-side evaluation gate. A client-supplied `passed` flag or threshold policy is not trusted.

**Production-composition status (incremental):** the API composition root still wires PostgreSQL identity/knowledge/memory, Redis and readiness probes; it does not yet compose the optional observability APIs, evaluation runner or Prompt rollout services. The generic `agent_platform.worker` remains the Outbox-to-Redis relay. A separate opt-in process, `agent_platform.observability.production_alert_worker`, now composes the durable PostgreSQL alert outbox, append-only audit writer, allowlisted HTTPS notification provider and graceful polling worker. It is available through the Compose `alerts` profile. This is alert-delivery dispatch only: it does not compose a rule evaluator, establish a distributed evaluator lease, or wire evaluation/Prompt schedulers. Real endpoint delivery and provider credentials still require deployment evidence.

## 3. CI and automated validation

The repository CI contract is:

- `.github/workflows/ci.yml`: Python 3.11 and 3.12 full `unittest discover`, Ruff, mypy, plus the reusable platform integration workflow.
- `.github/workflows/platform-integration.yml`: ephemeral PostgreSQL 16/pgvector and Redis 7.4 services, with `AGENT_PLATFORM_TEST_DSN` and `AGENT_PLATFORM_TEST_REDIS_URL`.
- `.github/workflows/platform-release-gate.yml`: end-to-end/security/fault-recovery suites and static checks.
- `.github/workflows/security-focused.yml`: focused security regression suite.
- `tests/test_issue_16_a_16_03_delivery_readiness.py`: verifies the documented runtime/configuration/migration/CI inventory and strict production settings parser.

### Latest-main CI evidence at review time

- Main SHA reviewed: `7d2ff4aa3f90ec141f1df07e1923b44044a85b00`.
- The commit's check-run list contained only the zombie-run-pruner workflow; its current run was queued and prior runs were cancelled. Combined commit status was `pending` with no successful required CI/security/release-gate conclusion.
- PR #269 and PR #277 are merged; their historical `ci`, `security-focused` and `platform-release-gate` runs succeeded, including their PostgreSQL/Redis integration job. These are historical component evidence, **not** a substitute for a successful full CI run on the current main SHA.
- The PR created from this document must itself pass CI. A passing PR run does not retroactively prove that latest main passed.

## 4. Real-service / production evidence — not executed

Per task instruction, no real server, production credentials, external provider, or live endpoint was requested. The following remain explicit field acceptance items; mocks and unit tests are not accepted as substitutes:

| Field check | Evidence required from deployment owner | Status |
|---|---|---|
| PostgreSQL migration | Target DB migration ledger/checksums, pre/post schema validation and backup reference | Not run |
| Prometheus | Authenticated scrape from deployed API, expected counters/histograms, scrape target and timestamp | Not run |
| Langfuse | Real SDK connection, one correlated trace visible in the target project, redaction inspection | Not run |
| Alert delivery | Real allowlisted HTTPS provider delivery, provider receipt, retry/dead-letter and acknowledge/resolve audit trail | Not run |
| Evaluation | Real configured model endpoint and immutable dataset version; persisted report, quality metrics and release-gate evidence | Not run |
| Prompt rollout | Real tenant canary decision, traffic observation, rollback to prior immutable version and audit record | Not run |
| Multi-replica operation | Demonstrated single scheduler/lease ownership and no duplicate alert/evaluation execution | Not run |
| Recovery | Timed backup/restore and worker restart/retry evidence with measured RPO/RTO | Not run |

## 5. Acceptance conclusion

Repository artifacts, dependency declarations, schema migrations, deployment configuration and CI definitions are present and mapped above. This does **not** mean A-16-03 is fully accepted: the reviewed latest-main SHA did not have a successful full CI result, the production composition does not yet wire the optional observability services/workers, and all real-service checks remain not run. Keep the #267 checkbox open until a later main commit has green required CI and deployment owners attach the field evidence (or explicitly transfer each field item to a tracked deployment acceptance issue).
