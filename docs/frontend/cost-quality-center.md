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


## L3-T17 completion increment (Issue #747)

### Usage filters and attribution
- Usage summary, ledger and CSV export now accept `run_id`, `project_id`, and `agent_id` in addition to time range and `model_key`.
- Project and Agent are explicit business-analysis filters. They are not silently derived from the current user's department or membership.
- Summary exposes the existing server-side `by_project` and `by_agent` aggregates. Selecting a row applies that dimension to summary, ledger and CSV export; the CSV endpoint re-evaluates the request on the server.
- The quota reporting API remains tenant-scoped. It does not apply user/department predicates to usage reads. In `auth.enabled=false`, the middleware's `anon` principal therefore reads the full default-instance tenant ledger; only explicit user-selected business filters narrow the result. In `auth.enabled=true`, the API continues to require the authenticated principal and uses the tenant boundary supplied by identity. This endpoint currently does not implement finer per-user/department data scopes; do not interpret a Project/Agent filter as access control.

### Optional fields and non-fabrication
- UI can display cached/reasoning token splits, currency/cost basis/pricing version, estimate/settled state, status/error code, P50/P95 and first-token latency only if the response supplies those fields.
- Current quota usage ledger provides USD `cost_usd`, `source`, `price_card_version`, token input/output, runtime and attribution IDs. It does not provide cached/reasoning tokens, latency percentiles, TTFT, request success/error rates, pricing currency metadata or settled-state contract. Corresponding fields remain “—” rather than being derived from unrelated counters.
- Cost-by-Agent and cost-by-Project use existing `build_usage_report` aggregates. No per-agent/project amount is synthesized by the browser.
- Evaluation history retains server-returned metric fields and links to the existing Evaluation Run detail route for sample/evidence drill-down. Dataset/sample CRUD remains owned by #750; this Issue does not create a parallel evaluation dataset manager.
- The UI does not calculate RAG Recall@K, citation coverage, precision/recall or regression scores from case text. These are displayed only when returned by the evaluation report / run detail API.
- Task-type filtering and a stable model-key → model-template relation are not present in the verified usage API contract. The UI does not guess task type from Run metadata or treat a provider/model string as a template ID; those two drill-downs remain backend/registry-contract gaps.

### Dual-mode verification
- Added API regression tests using the no-auth `anon/default` principal. With no explicit project/agent filter, events from different project/agent dimensions are both returned; an explicitly supplied Project+Agent filter narrows the analytics result only.
- This proves the quota reporting route does not silently apply user/department membership filtering in no-auth mode. The authenticated path still requires a principal and is tenant-bounded by the existing API.
- CSV export uses the same server-side filters and authenticated request context; no client-side export of a previously fetched unscoped data array.

### Validation boundary
- Focused regression test added at `tests/test_issue_747_noauth_usage_scope.py`.
- CI is required to execute Python tests, frontend lint/typecheck/build and integration checks. No local checkout/runtime was available through this execution surface.
- Real device, production billing/provider and external-resource validation are skipped as requested.


## L3-T15 — Token / 费用统计卡片与趋势图

- 统计卡片继续消费服务端 Usage Summary 的 Token、请求量和费用汇总，不在浏览器重新计算汇总费用。
- 新增按 UTC 日期的 Token 与费用趋势，基于同一筛选条件下 Usage Ledger 返回的 `occurred_at`、`input_tokens`、`output_tokens`、`total_tokens` 和 `cost_usd` 聚合。
- 缺失/非法价格事件不加入费用合计，单独计数；某日所有事件均缺价时该日费用显示为不可用（—），不显示为 0 USD。
- 图表旁提供逐日明细表，明确 UTC 日期、Token 分项、费用及有价/缺价事件数。费用币种沿用 API 明确提供的 USD。
- 当前 Usage Ledger 请求上限为 1,000 条；达到上限时图表提示可能不完整，不声称覆盖全部事件。趋势仅表示筛选后实际返回的账本记录，不从 Summary 总量推算缺失日期。
- 新增纯函数测试覆盖日期聚合、跨日排序、缺价处理及非法时间戳过滤。
