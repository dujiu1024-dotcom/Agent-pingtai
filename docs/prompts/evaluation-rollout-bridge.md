# Eval-to-Prompt Lab rollout bridge (C-26-04)

## Purpose

This bridge lets an authorized operator use a persisted Model Eval comparison report to configure the existing #23 Prompt Lab rollout policy. It does not duplicate Prompt Lab routing, create prompt versions, or invoke models again.

## API

`POST /api/platform/v1/observability/evaluations/comparisons/{report_id}/promote`

Example body:

```json
{
  "prompt_id": "support-answer",
  "model_to_prompt_variant": {
    "model-v2": "candidate",
    "model-v1": "stable"
  },
  "baseline_model_key": "model-v1",
  "candidate_percentage": 10,
  "minimum_score_gain": 0.02
}
```

The report is loaded server-side with the authenticated tenant ID. The caller cannot submit or override report metrics, the recommended run, or the winner. The API requires both evaluation-management permission and Prompt Lab `publish` permission.

## Promotion rules

- The recommended model key/run ID must resolve to exactly one row in the persisted comparison matrix.
- The recommended run must have passed its evaluation gate.
- The operator explicitly maps the evaluation model keys to distinct active Prompt Lab variant names.
- The baseline must be a different variant in the same report; the winner and baseline need a common `average_score` or `pass_rate` metric.
- A configurable minimum quality gain must be met before changing routing.
- The default candidate traffic is 10%; 100% performs a full cutover. The bridge delegates policy persistence and append-only audit to the existing `PromptRolloutService` / `PromptRolloutRepository`.
- The operation is an explicit operator-triggered action. There is no background auto-promotion or implicit route mutation when an evaluation finishes.

## Boundaries

The Prompt rollout service validates that all target variants are active in the same tenant and writes the routing policy/audit event. Production hosts must inject the durable PostgreSQL Prompt rollout repository and a tenant-aware registry. The comparison service must be configured with its tenant-scoped report repository (C-26-03). No provider, production database, or external resource is contacted by this bridge itself.
