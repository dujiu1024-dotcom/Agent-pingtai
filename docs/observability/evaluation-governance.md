# Evaluation governance and release evidence

## Human scoring
Human score writes and reads must be authorized by the dedicated evaluation-review
permission callback. The general evaluation-run permission is not a substitute.
Tenant scoping remains enforced in repository queries. API responses replace raw
reviewer account IDs with stable opaque aliases; score rationale is redacted before
persistence. Do not put prompts, model credentials, or raw user data in rationale.

## Regression comparison
Compare two persisted runs belonging to the same tenant. Higher-is-better metrics
include retrieval/answer/citation quality and pass rate; lower-is-better metrics
include latency, cost, forbidden-term rate and errors. Unknown metrics are compared
numerically but do not independently determine direction. The comparison stores
baseline/candidate run IDs, per-metric deltas and regression names.

## Release gate
Policies are immutable value objects with explicit IDs and positive versions.
Missing required metrics fail closed. A release decision may combine threshold
checks with baseline regression checks. Every decision records the policy version,
run IDs, failure reasons, comparison ID, UTC timestamp, caller-supplied evidence
references and SHA-256 digest of the canonical decision payload. Evidence refs
should identify CI artifacts, evaluation reports and human-review records.

## Platform API composition

The release-gate and regression endpoints are exposed only when the platform
composition root receives both `evaluation_governance_service` and
`evaluation_gate_manage_authorizer`. The factory rejects a partial configuration
rather than silently leaving a governance service inaccessible or exposing an
unprotected gate. The authorization callback is evaluated for each compare/gate
request; a false result or callback failure must not reach the governance service.

- `POST /api/platform/v1/observability/evaluations/{eval_run_id}/compare`
- `POST /api/platform/v1/observability/evaluations/{eval_run_id}/gate`

The gate request supplies a versioned policy (minimums/maximums), optional
baseline run ID, and non-empty evidence references. Tenant identity is taken from
the authenticated principal, never from request JSON. Keep evidence references
resolvable to immutable CI/evaluation/review artifacts.
