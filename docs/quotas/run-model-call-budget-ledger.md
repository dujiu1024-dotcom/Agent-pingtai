# C-72-04 Run 与模型调用预算持久化

本任务新增独立的 PostgreSQL Run 预算账户与模型调用账本，和既有 period/scope quota ledger 分工：后者负责租户/项目/Agent/模型维度周期额度，本账本负责单个 Run 的预算快照与每次模型调用的估算/实际对账。

## 生命周期

1. Run 启动时调用 `PostgresRunBudgetLedger.configure`，持久化 token/cost 上限。
2. 每次 Provider dispatch 前调用 `reserve`，以 Run 行锁串行化预留；记录模型、估算 input/output token 和估算成本。超限请求也留下 rejected 记录与原因，再抛出 `RunBudgetExceeded`。
3. Provider 返回后调用 `settle`，以调用行锁保证只结算一次；释放预留并累计实际 token/cost，保存估算差异、结算后剩余额度及实际超限原因。
4. `snapshot` 返回持久化上限、预留、实际用量和剩余额度。

## 一致性与边界

- 数据库事务和 `FOR UPDATE` 锁保护并发预留及结算；call_id 是幂等键。
- 结算重放只有在实际用量完全一致时才返回既有记录；不同用量重放拒绝。
- 实际用量超过预留或 Run 上限时仍如实结算并持久化超限原因，不能丢失 Provider 已发生的成本。
- 该模块不调用 Provider、不创建真实资源；宿主接线须在 dispatch 前 reserve、Provider 返回后 settle。当前交付提供持久化契约和实现，未宣称所有生产模型调用入口已完成接线。


## A-72-03 运行详情与审计查询

- Run 详情 API 在宿主注入 PostgresRunBudgetLedger 后，附加预算上限、预留、实际 token/cost、剩余额度、超限原因，以及最近 200 条模型调用估算/实际明细。
- 平台审计 API 提供 GET /api/platform/v1/audit/run-budget-events?run_id=...，只读返回该 Run 的每次调用估算 input/output/cost、Provider 实际 input/output/cost、差异、结算状态和超限原因。
- 两条查询均使用已认证 Principal 的 tenant_id，不接受调用方指定 tenant；Run 详情还必须通过现有 Run read 授权。审计查询要求显式 audit read 授权。未配置读取器时 fail-closed 返回 503。
- 尚未配置预算的 Run 在详情中返回 budget: null 和空调用列表，不影响读取普通 Run 状态。
