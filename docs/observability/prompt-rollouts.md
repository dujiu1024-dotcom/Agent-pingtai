# Prompt rollout and traffic routing (C-23-03)

PromptRolloutService implements deterministic, auditable request routing without changing the Agent runtime contract.

## Policy precedence

1. Explicit tenant route (tenant_routes) wins.
2. First matching user tag in lexicographic order (tag_routes) wins.
3. Otherwise a SHA-256 bucket derived from tenant, prompt, and caller-provided stable routing key selects a weighted variant. The same identity remains sticky while the policy revision is unchanged.

Weights must be finite, non-negative and sum to 100. Every configured target must be an active variant in the policy owner's tenant namespace. A policy is stored under its owner tenant; cross-tenant routing requires the caller to explicitly name that policy owner.

## Manual and automatic cutover

- Manual cutover sets a single active variant to 100%.
- Automatic promotion consumes a Prompt A/B report, selects the best candidate by average score, pass rate and latency, and requires the configured minimum score gain over the baseline. Failed thresholds do not mutate the policy.
- Every policy update increments a revision and writes a prompt audit event. Every route decision records the selected variant, matched rule, policy revision and a SHA-256 subject hash; raw user identifiers/tags are not persisted.
- PostgreSQL policies are durable. Route decisions are append-only and also emit to the existing prompt audit stream.

## Boundaries

The service returns a variant decision for the host runtime to resolve/execute. It does not call a model, alter runtime execution, or infer tenant identity from request payloads. The in-memory repository is a test/reference adapter; production should inject PostgresPromptRolloutRepository. Real traffic, production tenant authorization and live automatic promotion remain environment acceptance work, not claimed by unit tests.
