# Run 运行快照与外部副作用幂等实现说明

## 1. 注册版本快照

- `RunRegistrySnapshotService` 根据显式引用解析 Agent、模型模板、工具、Skill 和 Workflow 的 active 发布版本。
- 每条快照包含 kind、item_id、version 和完整定义；整体 canonical JSON 计算 SHA-256。
- `PostgresRunSnapshotStore` 将快照一次性绑定到 tenant_id + run_id；不同 digest 的重复写入拒绝。
- `LangGraphRunExecutor` 在 Run 进入 RUNNING 前加载固定快照；缺少快照时拒绝启动，避免 Run 已启动但版本上下文缺失。
- 恢复执行读取同一快照，不随注册中心 active pointer 变化而漂移。

## 2. 工具副作用幂等

代码：
- `agent_platform/tools/idempotency.py`
- `agent_platform/tools/gateway.py`
- `agent_platform/storage/postgres/013_tool_effect_idempotency.sql`

对于 EXTERNAL_OR_IRREVERSIBLE 和 FINANCIAL_OR_SECURITY 风险工具：
- 必须提供持久化 Idempotency Store、run_id 和 call_id。
- 键为 tenant_id + run_id + call_id；记录 action digest 和 actor_id，防止同一键被不同动作复用。
- 在副作用前原子 claim；并发重复请求只有首次 claim 能进入 handler。
- succeeded 重放返回缓存结果，不再次调用外部系统。
- pending/failed 表示执行结果可能不确定，拒绝自动重试，必须先对账/人工恢复。
- handler 成功后保存 JSON 结果；若结果落库失败，记录仍保持 pending，宁可阻止重复执行也不猜测外部副作用是否成功。
- 审批校验仍由原有 SupervisedPolicy 和一次性 ApprovalVerifier 负责；幂等账本不替代审批。

## 3. Worker 租约

- PostgreSQL Run State Store 通过租约、SKIP LOCKED 和 heartbeat 管理 Worker 所有权。
- 新 Run 在 QUEUED 状态允许先领取租约，再转为 RUNNING。
- Worker 丢失租约后取消本地执行，不允许失去租约的 Worker继续写终态。
- 真实进程崩溃、真实 PostgreSQL LangGraph Checkpoint 恢复和外部系统对账演练仍需 CI/部署环境验证。

## 4. 迁移

- `013_tool_effect_idempotency.sql`：工具副作用幂等账本。
- `014_run_registry_snapshot.sql`：Run 注册版本快照。
