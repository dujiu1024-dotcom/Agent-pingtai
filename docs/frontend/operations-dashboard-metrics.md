# UI-G2-744 运营驾驶舱指标字典与 API 核实

本页面只展示由已核实 API 返回的数据。页面不以客户端推算值冒充任务/运行指标；未有服务端契约的区块明确显示 API 缺口。

## 已接入数据

| 页面指标 | API | JSONPath | 类型 / 单位 | 口径与筛选 | 刷新 |
|---|---|---|---|---|---|
| 用量事件数 | `GET /api/platform/v1/quotas/usage/summary?start=&end=&model_key=` | `$.events` | integer / events | 指定时间范围内 ledger UsageEvent 数；受 tenant + auth principal scope 约束；模型过滤可选 | 60s |
| Input Token | 同上 | `$.input_tokens` | integer / tokens | UsageEvent.input_tokens 求和 | 60s |
| Output Token | 同上 | `$.output_tokens` | integer / tokens | UsageEvent.output_tokens 求和 | 60s |
| Total Token | 同上 | `$.total_tokens` | integer / tokens | input + output；不是任务数 | 60s |
| 估算成本 | 同上 | `$.estimated_cost_usd` | decimal string / USD | source=estimated；不与 Provider 实报混加 | 60s |
| Provider 实报成本 | 同上 | `$.provider_reported_cost_usd` | decimal string / USD | source=provider_reported | 60s |
| 对账成本事件 | 同上 | `$.reconciled_event_cost_usd` | decimal string / USD | source=reconciled；与估算/实报分别展示 | 60s |
| 模型用量分布 | 同上 | `$.by_model[model_key].{events,input_tokens,output_tokens,cost_usd}` | object / tokens, USD | UsageEvent 按 model_key 聚合 | 60s |
| Agent 活动排行 | 同上 | `$.by_agent[agent_id].{events,runtime_ms,total_tokens,cost_usd}` | object / ms, tokens, USD | 按 Agent 归因用量聚合；runtime_ms 是累计运行时长，不是并发负载/在线状态 | 60s |
| 每日 Token 趋势 | `GET /api/platform/v1/quotas/usage?start=&end=&model_key=` | `$.items[*].{occurred_at,input_tokens,output_tokens}` | integer / tokens | 前端按事件 occurred_at 的本地日期聚合；不用于财务结算 | 60s |
| 每日成本趋势 | 同上 | `$.items[*].{occurred_at,cost_usd,source}` | decimal string / USD | 按事件日聚合 cost_usd；保留 source 区分；币种固定 USD | 60s |
| 容量健康 | `GET /api/platform/v1/observability/health/capacity` | `$.status` 及 monitor 返回字段 | enum / monitor-defined | 仅在 operational_health_monitor 配置时存在；status 仅允许 ok/warning/critical/unavailable | 30s |
| 未关闭告警数 | `GET /api/platform/v1/observability/alerts?limit=100` | `$.items.length` | integer / alerts | 当前用户 tenant 下 open alerts 数；需 alert_store 配置 | 30s |

### API 真实响应约束

- `quotas/usage` 返回 `{tenant_id, items:[...]}`；事件字段由 UsageEvent 序列化，含 event_id、run_id、model_key、input_tokens、output_tokens、total_tokens、cost_usd、project_id、agent_id、runtime_ms、price_card_version、source、occurred_at。
- `quotas/usage/summary` 返回 `tenant_id`、`period_start`、`period_end` 与 `build_usage_report()` 聚合字段；金额是十进制字符串。
- 两个 quotas endpoint 当前均从 Principal 取得 tenant_id，并非 auth=false 无身份 API。若当前部署未完成 #755 的路由 no-auth 适配，前端显示 API 错误及 request_id，不伪造无身份访问成功。
- 时间参数必须是带时区的 ISO 8601；服务端限制范围不超过 366 天。页面默认浏览器本地时区生成边界并以 ISO UTC 发送；图表按浏览器本地日期分桶。
- `observability/health/capacity` 和 `observability/alerts` 需要相应 monitor/store 注入且当前 handler 强制 Principal。缺失配置或未授权时展示错误状态。

## 明确的 API 缺口（不渲染假数字）

| 需求 | 当前核实情况 | 需要的服务端契约 |
|---|---|---|
| 任务吞吐趋势 / completed_today / failed_today / success_rate | workbench tasks 是身份保护的列表/操作 API；未核实全局指标时间序列或统一成功率分母 | dashboard metrics 聚合 endpoint；定义任务去重键、状态枚举、取消/运行中是否纳入、时间桶及时区 |
| running_run_count / queued_task_count / queue_depth | 未核实可直接用于驾驶舱的稳定快照与时间序列 | scheduler/queue metrics endpoint；明确快照时间、队列/worker 维度与刷新延迟 |
| Agent enabled/available 与实时负载 | usage summary 的 by_agent 仅是用量归因；runtime_ms 不能代表并发、心跳或 availability | Agent registry/runtime health endpoint；定义 enabled、available、heartbeat age、running count 与并发上限 |
| 失败原因 TopN | 没有已核实的统一 failure_code 聚合 API | Run/Task failure analytics endpoint；标准化 failure_code、原因分类、聚合窗口、去重 run_id 与下钻 run_id |
| pending_approval_count | approvals list 是用户授权后的待审批明细，未核实全局聚合统计 API | approval summary endpoint；定义 pending 状态、可见范围和计数口径 |
| 项目筛选 | usage report 只公开 start/end、run_id、model_key 参数；无 project_id 查询参数 | 在服务端加入授权感知的 project_id 过滤 |
| Agent 筛选 | usage report 暂无 agent_id 查询参数 | 在服务端加入授权感知的 agent_id 过滤 |

## 错误、空值与安全

- 空结果显示“所选时间范围暂无用量事件”，不显示 0 作为缺失数据替代。
- 请求失败保留稳定错误信息与 request_id，并提供重试。
- auth=true 的 tenant/principal 资源隔离由后端执行；前端筛选不能替代授权。
- 不缓存会话敏感数据到长期浏览器存储。React Query 只在内存中缓存本页短时查询结果。
- 不配置任何模拟 KPI、mock 事件或硬编码成本/成功率。
