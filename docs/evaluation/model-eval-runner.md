# Model Eval Runner (C-26-02)

The Eval Runner pins one tenant-owned Prompt string and one immutable dataset version, then executes every requested model variant against every case.

## API

`POST /api/platform/v1/observability/evaluations/variants/run`

```json
{
  "dataset_id": "support",
  "dataset_version": 1,
  "prompt": "Answer the question: {{input}}",
  "model_variants": ["provider/model-a", "provider/model-b"]
}
```

Tenant identity comes from the authenticated principal. The dataset repository loads the exact version under that tenant. The model executor is injected by the host and must resolve each variant through its approved model registry/provider routing; this module does not accept credentials or arbitrary endpoint URLs.

## Scoring

- `exact`: normalized exact output match.
- `contains`: expected output appears in the model output.
- `keywords`: fraction of rubric keywords found.
- `llm_judge` and `custom`: require an injected judge implementation. Scores must be in [0, 1]; the rubric may set `pass_threshold`.

## Metrics and persistence

Each variant result records pass rate, average score, input/output/total tokens, mean/P50/P95 latency and total cost. Cost is reported only when the model adapter returns explicit provider cost for every case; unknown/incomplete cost remains null rather than being guessed. Prompt content is not persisted; only its SHA-256 fingerprint and dataset/version reference are stored.

PostgreSQL uses `platform_eval_variant_runs`, keyed by tenant and run ID and foreign-keyed to the immutable dataset version. The API returns per-variant aggregates and run ID. Comparison matrices/recommendations and PDF/HTML exports remain C-26-03; automatic routing remains C-26-04.
