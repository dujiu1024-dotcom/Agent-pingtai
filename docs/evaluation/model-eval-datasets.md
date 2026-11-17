# Model Eval dataset management

The Model Eval dataset catalog stores immutable, tenant-scoped versions of prompt evaluation cases.

## Case format

Each case contains:
- `case_id`: stable identifier unique within one dataset version
- `input`: prompt/input presented to a model
- `expected_output`: reference output
- `rubric`: non-empty JSON object consumed by the scoring layer
- `source_ref`: optional opaque reference to the source artifact/version

CSV imports require the headers `input,expected_output,rubric`; `case_id` and `source_ref` are optional. The rubric cell contains a JSON object. JSONL imports use one JSON object per non-empty line with the same field names.

## API

All endpoints require an authenticated principal and the evaluation manager authorization callback. Tenant identity and creator identity are taken from the server-side principal; request payloads cannot select either value.

- `POST /api/platform/v1/observability/evaluations/datasets/import` — JSON body: `{ "dataset_id": "support", "format": "jsonl", "content": "..." }`. Omit `dataset_id` to allocate one.
- `GET /api/platform/v1/observability/evaluations/datasets/{dataset_id}/versions` — list versions in the caller's tenant.
- `GET /api/platform/v1/observability/evaluations/datasets/{dataset_id}/versions/{version}` — retrieve one version.
- `GET /api/platform/v1/observability/evaluations/datasets/{dataset_id}/versions/{version}/export?format=csv|jsonl` — export a version.

Each import creates the next version. Existing versions are never overwritten. PostgreSQL version allocation is serialized by a transaction-scoped advisory lock keyed by tenant and dataset ID. Composite primary/foreign keys bind every case to the same tenant, dataset and version.

## Limits and boundaries

- Import body: 2 MB maximum.
- Cases per version: 1–2,000.
- Input and expected output: 20,000 characters each.
- Rubric: JSON object, 10,000 serialized characters maximum.
- Source reference: 2,048 characters maximum.
- The CSV/JSONL interchange shape is intentionally compatible with common promptfoo-style input/expected-output datasets, without importing promptfoo's provider execution semantics here.
- This task implements dataset management only. Multi-model execution, score aggregation, comparison reports and automatic routing remain C-26-02 through C-26-04.
