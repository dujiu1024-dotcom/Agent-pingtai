# Session 与长期记忆的写入边界（C-25-04）

## 职责划分

- **Session 是短期事实源**：完整消息、角色、时间顺序、Run/Tool 关联和恢复所需上下文只写入 Session 消息存储。Session 保留原始对话，不复制到长期记忆。
- **Memory 是长期沉淀层**：只接收从单个已完成交互轮次中提炼的、可复用的事实/偏好/决策摘要。长期记忆保留来源引用、作用域、置信度和候选/审核状态，不保存整段聊天记录。
- **Run context 是执行期状态**：仅供当前执行和 checkpoint 恢复，不因 Run 完成而自动成为长期记忆。

## 写入时机

1. 用户消息先持久化到 Session；此时不触发长期记忆提取。
2. Agent 流式生成期间的 token、partial assistant event、工具中间结果和未完成响应不触发长期记忆提取。
3. 只有最终 assistant 消息成功持久化、且它是 Session 当前最新消息时，调用 POST /api/platform/v1/sessions/{session_id}/memory-extract。
4. API 只取最近一组 user + final assistant 消息构造单轮 MemoryTurn。没有最终 assistant 消息、最新消息不是 assistant、或缺少前置 user 消息时返回冲突，不回退提取更早轮次。
5. 提取结果先写入 candidate，绝不自动 confirm / activate。必须经过 #11 现有候选审核、确认和权限治理流程后，才可被长期记忆检索。
6. 同一 assistant message 的来源 URI 固定为 session://{tenant_id}/{assistant_message_id}@1。Memory 只保留该 pinned provenance，不复制 session transcript；重复请求按来源和确定性 ID 幂等。

## 边界与失败语义

- MemoryTurn.completed 必须显式为 true；提取器只接收一轮 user/assistant 文本，不接收整个 transcript。
- source reference 必须是 canonical session:// URI，且 tenant 与认证 principal 一致；跨租户来源拒绝。
- 用户级候选只能写入当前用户 scope；Agent/Project/Tenant scope 继续服从 #11 的管理授权。
- 输入超长、无效候选、无来源、权限不足或 extractor 失败时不写入任何长期记忆。候选批次先完整预检，再逐项写入。
- 提取服务/审核服务不可用时，Session 消息仍是权威数据；不得为了保证记忆写入而阻塞或回滚已完成对话。由调用方在完成轮次后重试提取。
- 删除 Session 不等价于自动删除已审核的长期记忆；长期记忆通过其自身来源引用、保留策略和 #11 遗忘/删除传播流程治理。

## API 调用契约

调用方必须在最终 assistant 消息事务提交后触发提取。不得从 token callback、消息流增量事件或用户消息写入回调触发。API 是候选生成入口，不是自动批准入口；审核完成前候选不可被 Memory retrieval 返回。

## 自动化覆盖

tests/test_memory_boundaries.py 覆盖完成轮次才提取、未完成轮次拒绝、来源 tenant pinning、候选不自动激活以及同一来源幂等。生产 PostgreSQL、异步 Worker 和真实模型提取器的现场验证不由这些单测替代。
