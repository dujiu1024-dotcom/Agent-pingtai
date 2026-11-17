# Model Eval Variant Comparison and Reports (C-26-03)

C-26-03 consumes the persisted Eval Runner record from C-26-02. It never reruns model calls or mutates the underlying evaluation result.

## Endpoints

- `GET /api/platform/v1/observability/evaluations/variants/{eval_run_id}/comparison`
- `GET /api/platform/v1/observability/evaluations/variants/{eval_run_id}/report?format=html`
- `GET /api/platform/v1/observability/evaluations/variants/{eval_run_id}/report?format=pdf`

All endpoints require an authenticated principal and evaluation-management authorization. The repository lookup is scoped by the principal tenant; cross-tenant or unknown run IDs return 404.

## Matrix and recommendation

The matrix includes rank, model variant, case/pass counts, pass rate, average score, input/output/total tokens, explicit total cost and mean/P50/P95 latency. Cost remains unknown when any case lacks provider cost; it is not estimated.

Recommendation is deterministic and explainable: average score descending, pass rate descending, known cost ascending, average latency ascending, total tokens ascending, then model key ascending. Quality is primary; efficiency breaks quality ties. The report exposes the ranking policy and reason strings.

## Exports

HTML is UTF-8, self-contained, and escapes model-provided text. PDF is generated with the Python standard library and built-in Helvetica font, so no external renderer or service is required. The PDF summary uses the built-in WinAnsi font; non-Latin glyphs are replaced in PDF text while the HTML export preserves Unicode.
