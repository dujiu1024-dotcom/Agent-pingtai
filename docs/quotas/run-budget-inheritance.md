# C-72-05 Run 预算继承、恢复与幂等扣减

## 持久化谱系

迁移为 `platform_runs` 增加 `parent_run_id`、`budget_root_run_id`、`budget_lineage_kind` 和 `budget_lineage_key`。数据库触发器从父 Run 或 retry source 推导预算根，并拒绝跨租户父 Run、伪造 root 或重置 root。旧 retry_of_run_id 链在迁移时回填到最初根 Run。

## 扣减规则

- 根 Run 的预算根是自身；retry Run 继承被重试 Run 的根；委派子 Run 继承父 Run 的根。
- 恢复继续使用同一个 Run 与模型调用 call_id；不能因为 worker 恢复而创建新预算根或新扣减标识。
- 真正的新 retry/model-call 使用新的 call_id，但仍计入同一个 root Run 预算。
- `RunBudgetInheritanceService` 从持久化 RunStateStore 读取 root，而不是信任请求 metadata；要求配额范围包含且仅包含正确的 root Run scope。
- `QuotaReservationGroupService` 用 call_id 派生稳定 reservation/group UUID。重复提交同一 call_id 时，数据库主键/内存 settled 集合拒绝第二次预留；group settle 保持既有原子结算语义。
- 委派 executor 在子 Run 持久化后登记 parent/root 谱系，并验证子请求 root 与父 Run 一致。

## 验证边界

单测覆盖 root scope 强制、child 归集、重复 call_id 防双扣、缺失谱系 fail-closed。PostgreSQL trigger/migration 需由 CI 数据库集成检查；不执行真机或外部 Provider 验证。
