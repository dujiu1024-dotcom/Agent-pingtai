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


## L4-T16 performance metrics view

- Adds a dedicated **性能** tab to the existing Cost & Quality Center; no parallel backend metrics API or telemetry recorder is introduced.
- Reads the existing authenticated `GET /platform/v1/observability/metrics/json` endpoint through the shared API client and renders the returned metric families, sample values, types, help text and bounded labels.
- Reuses the existing quota usage summary for request count, success/error rates, P50/P95 latency and first-token P50 only when the response actually contains those fields.
- Current verified `build_usage_report` contract does not provide request count, success/error rates or latency percentiles. The performance view therefore displays these as unavailable. The metrics endpoint exports Prometheus snapshots, not a time-series query; cumulative counters are not presented as requests/second. Throughput remains unavailable until a server-side rate/window contract exists.
- The Prometheus table is a current collector snapshot and is explicitly not represented as matching the selected date/model filter. The quota summary cards do honor the existing date/model filters.
- Loading, request error, empty collector and unavailable metric states are explicit. No metric values are synthesized from ledger event counts or runtime totals.
- Verification: source/API contract reviewed against `agent_platform/api/observability.py`, `agent_platform/observability/metrics.py`, `agent_platform/observability/prometheus.py` and `agent_platform/quotas/usage_reporting.py`. This connector-only environment has no local frontend runner; CI is required for lint, typecheck, build and automated tests. No real-device or production-resource validation is claimed.


## L2-T16 — 评测结果页（Dataset / Runs / 指标对比）

- `EvaluationResultsPanel.tsx` 由成本与质量中心“质量”分区挂载；不改变成本、性能和配额分区。
- Dataset 目录：`GET /platform/v1/observability/evaluations/datasets?limit=100`；版本：`GET /platform/v1/observability/evaluations/datasets/{dataset_id}/versions`。目录只显示服务端返回的数据集，版本列表展示版本号、样本数、创建者和创建时间。
- Evaluation Runs：`GET /platform/v1/observability/evaluations?dataset_id={id}&limit=100`；按所选 dataset version 筛选报告，指标从报告原始 `report` JSON 的顶层字段及 `metrics` 对象读取，不推算缺失值。
- 指标比较：选择同一 Dataset 版本的 2–32 个运行后调用 `POST /platform/v1/observability/evaluations/comparisons`，由服务端比较服务生成并持久化报告；可通过 `GET .../comparisons/{report_id}` 回读，并通过 `GET .../comparisons/{report_id}/export?format=html` 导出。
- 比较表使用服务端 `metric_names`、`variants`、`comparisons_to_recommended` 与 `recommended`；差值只呈现共同指标。服务端比较算法的推荐顺序和口径不在前端重算。
- 页面仅提供 Dataset/Run 管理页链接，不复制 #803 的 Dataset/Case 编辑、导入、样本证据和评测执行功能。所有目录、版本、运行和比较 API 均由服务端按当前 Principal/tenant 授权；前端不传 tenant/user 身份字段。
- 验证边界：本次通过 GitHub 主线源码核对 API 路径、响应结构和服务端 tenant 过滤；本执行环境没有仓库 checkout 与 pnpm/Vitest runner，未声称本地 lint/typecheck/build 已通过。CI 以本 PR 最新 HEAD 的 GitHub Actions 结果为准；不做真机或生产资源验证。
