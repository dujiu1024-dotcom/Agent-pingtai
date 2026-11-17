# Model Eval → Prompt Lab rollout bridge

Issue #523 / C-26-04 connects a completed tenant-scoped Prompt A/B experiment to the Prompt Lab rollout policy.

## API

`POST /api/platform/v1/evaluations/{experiment_id}/rollout`

The host must inject:
- an `EvaluationRolloutBridge` backed by the same tenant-scoped experiment report repository and Prompt Lab `PromptRolloutService`;
- an authorizer that checks the authenticated Principal can publish the report's prompt;
- an append-only audit writer.

Example automatic promotion:

```json
{"mode":"automatic","rollout_percentage":10,"minimum_score_gain":0.03}
```

Example operator-triggered cutover:

```json
{"mode":"manual","variant":"candidate","rollout_percentage":25}
```

The tenant and actor are always taken from the authenticated Principal, never the request body. The report is loaded using that tenant. The bridge rejects failed cases, incomplete per-variant case coverage, missing baseline/variant lineage, non-finite values, and candidates below the configured score gain. Automatic mode chooses the highest score, then pass rate, then lower latency; it creates a canary weight split against the baseline. A 100% rollout is an explicit full cutover. Manual mode still requires a variant present in the same experiment and an authorized prompt publisher.

Every request writes an audit intent before mutating the rollout policy; the PromptRolloutService persists the policy update and its own append-only audit record. Rollout target validation is delegated to PromptRolloutService, which rejects missing or inactive variants. The bridge does not call model providers or change prompt content.

## Composition

Pass `evaluation_rollout_bridge`, `evaluation_rollout_authorizer`, and `resource_audit_writer` to `create_platform_app` together. The route is mounted only when all dependencies are supplied. No external model, server, Vault, or production resource is contacted by this feature.
