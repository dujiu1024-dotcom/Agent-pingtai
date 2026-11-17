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
