# C-72-05 Run 预算继承、恢复与幂等扣减

## 持久化谱系

迁移为 `platform_runs` 增加 `parent_run_id`、`budget_root_run_id`、`budget_lineage_kind` 和 `budget_lineage_key`。数据库触发器从父 Run 或 retry source 推导预算根，并拒绝跨租户父 Run、伪造 root 或重置 root。旧 retry_of_run_id 链在迁移时回填到最初根 Run。

## 扣减规则

- 根 Run 的预算根是自身；retry Run 继承被重试 Run 的根；委派子 Run 继承父 Run 的根。
- 恢复继续使用同一个 Run 与模型调用 call_id；不能因为 worker 恢复而创建新预算根或新扣减标识。
- 真正的新 retry/model-call 使用新的 call_id，但仍计入同一个 root Run 预算。
- `RunBudgetInheritanceService` 从持久化 RunStateStore 读取 root，而不是信任请求 metadata；要求配额范围包含且仅包含正确的 root Run scope。
- `QuotaReservationGroupService` 用 call_id 派生稳定 reservation/group UUID。重复提交同一 call_id 时，数据库主键/内存 settled 集合拒绝第二次预留；group settle 保持既有原子结算语义。
- 委派 executor 将 parent/key 直接传入任务创建；Run 行在同一事务中写入谱系，且在 `dispatch_ready` 置位前由数据库触发器推导 root。Executor 随后核对持久化 child root 与 parent root。

## 验证边界

单测覆盖 root scope 强制、child 归集、重复 call_id 防双扣、缺失谱系 fail-closed。PostgreSQL trigger/migration 需由 CI 数据库集成检查；不执行真机或外部 Provider 验证。


## A-72-02 并发、失败与重试扣减补充

- Tenant + root Run 预留在单个数据库事务中完成，并按稳定 scope 顺序锁预算行；任一 scope 超限时整组回滚。进程内适配器在同一把锁下校验并插入整组。
- `idempotency_key` 在同一 Run attempt 内派生稳定 group/reservation ID。相同 key 与相同预算参数重放返回原组，不再重复占用；同 key 参数不一致、组不完整或状态不一致时 fail-closed。
- 结算事件 ID 从 reservation group 派生。Provider 成功后若结算响应丢失，重放相同用量返回原账本事件；不同用量重放拒绝，避免重复计费或覆盖账本。
- 可证明 Provider 未 dispatch 的失败可释放整组；dispatch 结果未知时保留预留，由 lease heartbeat / stale-reservation recovery 管理，不能乐观释放已可能产生费用的请求。真正的新 retry attempt 必须使用新的幂等键，但继续使用相同 root Run 预算。
- PostgreSQL 集成测试覆盖跨 scope 原子回滚、并发争抢和组结算；本轮新增内存重放测试。生产多 worker 的真实数据库压力验证仍由 CI 集成环境负责，未执行真机/外部 Provider。
