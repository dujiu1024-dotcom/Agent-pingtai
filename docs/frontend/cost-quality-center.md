# Issue #747 — Cost & Quality Center (frontend increment)

## Route and ownership

- Route: `/workspace/quality-cost` (already registered in the canonical route map under owner #747).
- Rendered by `frontend/src/features/quality-cost/CostQualityCenter.tsx`.
- Navigation remains driven by the existing bootstrap-aware route configuration. The page does not create an independent auth bypass.

## Verified data contracts used

All requests go through the shared `apiClient` (base path `/api`, same-origin credentials, timeout/error normalization and ETag support).

| View | Request | Fields consumed |
|---|---|---|
| Usage summary | `GET /platform/v1/quotas/usage/summary?start=&end=&model_key=` | events, input/output/total_tokens, tool_calls, runtime_ms, storage_bytes, estimated/provider_reported/reconciled cost, variance, by_model, price_card_versions, tool_and_storage_pricing |
| Usage ledger | `GET /platform/v1/quotas/usage?start=&end=&model_key=&limit=1000` | event_id, run_id, model_key, input/output/total_tokens, cost_usd, project_id, agent_id, runtime_ms, source, occurred_at |
| Capacity health | `GET /platform/v1/observability/health/capacity` | status and returned probe payload |
| Evaluation history | `GET /platform/v1/observability/evaluations?dataset_id=&limit=20` | server-returned report objects, rendered without deriving metric values |

Source implementations checked: `agent_platform/api/quotas.py`, `agent_platform/quotas/usage_reporting.py`, and `agent_platform/api/observability.py`.

## Behavior

- Date range and model filter apply to both summary and event ledger.
- Summary and ledger refresh every 30 seconds; manual refresh is available.
- Costs are labelled USD because the API returns `cost_usd`; the UI keeps estimated, provider-reported, and reconciled event costs separate.
- Model ranking is derived only from the server-returned `by_model` cost aggregation.
- Evaluation reports are queried only after a Dataset ID is supplied.
- Loading, API error, empty data, and invalid date range states are explicit.
- Responsive layout and reduced-motion behavior are included.

## Deliberate non-claims / remaining API gaps

The current usage API does not expose P50/P95 latency, TTFT, cached/reasoning token splits, or cost currency/pricing metadata beyond USD and `price_card_version`. It also does not provide dedicated cost breakdown by Agent/project in the UI contract. The page therefore does not invent these metrics or derive them from unrelated fields. Tool/storage pricing is explicitly shown as excluded by the server report.

Evaluation history endpoint requires a known Dataset ID; the current API contract does not provide a unified dataset discovery/list endpoint in this router. The page does not add a fake dataset selector or launch evaluations without a verified model/dataset selection flow.

The capacity health endpoint is dependency-injected and may return 503 when no monitor is configured; this is presented as unavailable rather than as a healthy default.

## Verification boundary

- Source and API contract inspection completed from the PR base branch.
- No local repository checkout or browser runner was available in this connector-only execution; local pnpm lint/typecheck/build and browser tests were not executed.
- GitHub Actions on the final PR head are the CI source of truth. Real provider, production database, live model billing, and device validation are not claimed.
