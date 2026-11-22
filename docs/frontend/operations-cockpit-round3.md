# L3-T09 运营驾驶舱 Round 3 扩展

## 页面接线
- 在既有 `Dashboard` 下方挂载运营驾驶舱扩展区；复用现有 API client、TanStack Query、路由和全局状态。
- 时间范围 1/7/30/90 天通过浏览器本地时间生成 ISO 8601 边界；项目、Agent、模型筛选保存在 URL query，刷新/分享链接保留上下文。
- 模型条件传给已验证的 `GET /platform/v1/quotas/usage?start=&end=&model_key=`；项目和 Agent 只对该授权请求返回的事件集合本地二次筛选，因为服务端暂未提供对应查询参数。前端筛选不构成授权边界。

## 数据口径与刷新
| 区块 | 数据源 / JSONPath | 口径 | 刷新 | 下钻 |
|---|---|---|---|---|
| Token 趋势 | quotas/usage `$.items[*].{occurred_at,total_tokens}` | 按浏览器本地日期对返回 UsageEvent 的 total_tokens 求和；非财务结算 | 60s | 任务列表并保留时间范围 |
| 风险告警 | observability/alerts `$.items[*]` | 仅 status=open/acknowledged；最多展示 5 条 | 30s | 告警中心 |
| 组件健康 | observability/health/capacity `$.status` | 只显示服务端 capacity 状态，不推断 worker 级健康 | 30s | 系统运维诊断 |
| Agent 用量 | quotas/usage `$.items[*].agent_id` | 当前筛选事件中的事件数，不代表并发/在线负载 | 60s | Agent 运行态详情 |
| 模型分布 | quotas/usage `$.items[*].model_key` | 当前筛选事件数 | 60s | 选择模型筛选 |
| 任务状态分布 | workbench/tasks `$.items[*].status` | 仅当前 API 响应样本，不代表全局任务聚合 | 30s | 任务列表状态筛选 |

金额字段仍沿用现有指标字典中的 estimated/provider_reported/reconciled 分列；本扩展不混合不同成本来源。

## API 缺口
当前未证实任务吞吐/成功率时间序列、失败原因 TopN、全局待审批计数、queue_depth、Agent enabled/available/并发/心跳、P50/P95、TTFT、逐组件健康等统一 API。页面明确列出缺口，不将缺失字段填 0 或从局部样本推断全局指标。

## 错误和空态
- 各数据源独立显示 Loading、Error + 重试、Empty；usage 错误展示可用 request_id。
- 刷新时间来自查询缓存更新时间；页面不把部分数据源成功描述为全量健康。
- 项目/Agent筛选选项来自当前时间范围已返回事件；服务端没有可供选择的全局目录契约。

## 验证边界
新增纯函数测试覆盖无筛选、组合筛选和缺失维度值；CI 执行 lint/typecheck/unit/build。浏览器真机、生产服务和外部资源验证按任务要求跳过。
