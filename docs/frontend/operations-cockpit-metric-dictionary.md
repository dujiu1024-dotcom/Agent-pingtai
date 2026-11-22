# L4-T101 运营驾驶舱指标字典与数据边界

## 页面接线

默认首页 `Dashboard` 现挂载 `OperationsCockpitExtension`。扩展复用统一 API client、TanStack Query、现有路由和服务端授权响应；不创建新的后端指标服务。筛选参数 `days/project/agent/model` 保留在 URL。

时间窗按浏览器本地日历计算，再以 ISO-8601 时间戳发送服务端。Token 趋势按浏览器本地日期分桶。项目/Agent 仅在服务端已授权返回的 UsageEvent 集合中本地二次筛选，绝不作为权限边界。成本 summary 的服务端过滤只支持时间与 model_key；项目/Agent 选择不会改变成本 summary，页面已明确标注。

## 指标字典

| metric_key | 中文名 | 公式/分子分母 | 数据源与 JSONPath | 单位/窗口 | 刷新 | 空值语义 | 下钻/限制 |
|---|---|---|---|---|---|---|---|
| `usage.total_tokens_by_local_day` | 每日 Token | 每个本地日对响应 `items[*].total_tokens` 求和；保留 API 事件粒度 | `GET /platform/v1/quotas/usage` → `$.items[*].{occurred_at,total_tokens}` | tokens；1/7/30/90 天 | 60s | 无事件显示空态，不显示伪造 0 | 点击柱跳转任务中心 `from/to` 本地日过滤；事件趋势不等同任务吞吐量 |
| `usage.cost.estimated_usd` | 估算成本 | 服务端 `estimated_cost_usd` 原值 | `GET /platform/v1/quotas/usage/summary` → `$.estimated_cost_usd` | USD；选定时间窗 | 60s | 缺字段显示 `—` | 成本与质量中心；estimated 独立口径 |
| `usage.cost.provider_reported_usd` | 供应商报告成本 | 服务端 `provider_reported_cost_usd` 原值 | 同上 → `$.provider_reported_cost_usd` | USD；选定时间窗 | 60s | 缺字段显示 `—` | provider-reported 独立口径，不与估算值相加 |
| `usage.cost.reconciled_event_usd` | 已核对事件成本 | 服务端 `reconciled_event_cost_usd` 原值 | 同上 → `$.reconciled_event_cost_usd` | USD；选定时间窗 | 60s | 缺字段显示 `—` | reconciled 独立口径，不与其他口径相加 |
| `alert.open_sample` | 未关闭告警样本 | 当前响应集合中 `status=open/acknowledged` 条数，列表最多 5 条 | `GET /platform/v1/observability/alerts?limit=100` → `$.items[*]` | 条；API 返回窗口 | 30s | 空集合显示空态 | 告警中心；不是未分页全局总数 |
| `capacity.status` | 容量健康摘要 | 直接显示服务端 `status`，不由前端推导 | `GET /platform/v1/observability/health/capacity` → `$.status` | 服务端枚举；当前快照 | 30s | 缺字段显示未知 | `/admin/operations`；不推断 Worker/逐组件健康 |
| `usage.agent_event_count` | Agent 用量排行 | 当前过滤事件中按 `agent_id` 计数 | Usage API → `$.items[*].agent_id` | 次；当前筛选窗口 | 60s | 无事件显示空态 | Agent 详情；不是在线/并发负载 |
| `usage.model_event_count` | 模型分布 | 当前过滤事件中按 `model_key` 计数 | Usage API → `$.items[*].model_key` | 次；当前筛选窗口 | 60s | 无事件显示空态 | 点击模型设置 `model` 筛选 |
| `workbench.task_status_sample` | 任务状态样本分布 | 当前任务响应中按 `status` 分组计数 | `GET /platform/v1/workbench/tasks` → `$.items[*].status` | 条；当前响应样本 | 30s | 空集合显示空态 | 按状态进入任务列表；不是全局队列 |
| `workbench.task_terminal_success_rate_sample` | 任务样本成功率 | 成功终态 /（成功终态 + 失败终态）；取消、排队、运行、审批等待、未知状态排除 | workbench/tasks → `$.items[*].status`，只计算 API 已返回样本 | %；当前响应样本 | 30s | 无成功/失败终态样本显示 `—` | 首页 KPI 明确标注样本成功率，不冒充全局成功率 |

## 下钻参数

- Token 日柱生成 `/workspace/tasks?from=YYYY-MM-DDT00:00&to=YYYY-MM-DDT23:59`，使用任务中心现有创建时间筛选。
- 模型排行通过 `model` URL 参数回写筛选；Agent 排行进入 Agent 详情；任务状态使用原始状态值筛选；告警进入告警中心；容量健康进入运维诊断。
- KPI 任务样本成功率的分母仅含成功与失败终态；取消与非终态不参与计算，避免将取消/运行中误计为失败。

## API 缺口（不显示推算值）

未找到可用于驾驶舱的统一服务端契约：Agent 总数/启用/在线/可用/心跳/并发；全局 Run 运行数；队列深度；全局任务吞吐/成功率时间序列；失败原因 TopN；全局待审批计数；P50/P95、TTFT；日级成本时间序列；逐组件/Worker 健康明细。缺口保持空缺，不用 mock、0 或局部样本代替。

## 加载、错误、过期与权限

各 API 查询独立显示 loading、error+retry、empty；若 API client 返回 request_id，则错误状态展示 request_id。更新时间取查询缓存最近成功更新时间；stale 数据显式提示。缺失字段用 `—` 或未知状态。数据范围由服务端授权响应决定；本地项目/Agent 筛选不提供任何访问控制。

## 回归测试

`operations-cockpit-metrics.test.ts` 覆盖终态成功率分母（排除取消/非终态）、无终态样本与日期下钻 URL；既有 `OperationsCockpitExtension.test.ts` 覆盖项目/Agent/模型组合筛选与缺失维度。真机/生产与外部资源验证按调度要求跳过。
