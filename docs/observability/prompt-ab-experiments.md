# Prompt A/B experiment report persistence (C-23-02)

Prompt experiments execute the selected active variants against the same immutable dataset snapshot. The executor and optional LLM judge remain host-injected; the service records the per-case matrix, rule/judge/final scores, variant version, latency, cost, and baseline comparison.

## Storage

Use `PostgresPromptExperimentRepository(connection_factory)` for durable reports. The repository writes a single JSONB report row keyed by `(tenant_id, experiment_id)`; reads always include the tenant predicate. Migration `096_prompt_experiment_reports.sql` adds the table and recent-report index. Rows are append-only: a database trigger rejects UPDATE/DELETE and PUBLIC is denied those privileges. Duplicate experiment IDs are rejected by the primary key rather than silently overwritten.

## Safety and limits

Report serialization rejects NaN/Infinity and non-JSON comparison payloads. The repository does not store rendered prompts, input variables, or raw model outputs separately; the matrix stores scores, latency, cost, and case/variant/version identifiers. Deployments must provide tenant-scoped DB credentials and run the migration before selecting the PostgreSQL adapter. The in-memory repository remains intended for tests and ephemeral local use.

## Verification boundary

Unit tests cover round-trip serialization, tenant isolation, invalid numeric payload rejection, and duplicate behavior through a database adapter double. A real PostgreSQL migration/permission test and live model/LLM-judge repeatability run remain deployment/CI evidence; deterministic fake providers do not prove production score variance.
