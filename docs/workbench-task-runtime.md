# 工作台任务中心：真实 Run 执行接线

## 交付范围

工作台任务不再由外部临时实现一个空的 `TaskCenterService`。平台提供：

- `RegistryBackedTaskCenterService`：从租户注册中心解析已发布 Agent、模型模板、模型供应商、工具、Skill 和工作流；提交时创建真实 Run，并将所有依赖版本固定到 Run 注册快照。
- `PostgresRunStateStore`：持久化任务输入、输出、状态、执行者、会话/项目关联和幂等键。
- `RunExecutionWorker`：通过 PostgreSQL 租约领取任务，调用既有 `LangGraphRunExecutor`，持久化结果，并在进程重启后恢复可恢复的 Run。
- `create_registry_backed_task_service_resolver`：将租户隔离的 Registry、快照服务和 Run 生命周期组装成工作台服务。
- `create_platform_app(task_worker=...)`：应用启动时启动 Worker，关闭时停止 Worker；未提供 Worker 时不允许发布 `task_execution=true` 能力。

## 注册中心 Agent 定义约定

已发布 Agent 的 definition 至少包含：

```json
{
  "instructions": "Agent 的系统指令",
  "model_template_id": "general",
  "tool_ids": ["search"],
  "skill_ids": ["summarize"],
  "workflow_id": "research-flow"
}
```

`tool_ids`、`skill_ids` 和 `workflow_id` 可省略。模型模板 definition 必须包含 `provider_id` 和 `provider_version`。被引用的供应商版本必须已发布，且必须仍是该供应商的活动版本；否则任务提交失败关闭。凭据只通过供应商的租户级 `secret_ref` 解析，不写入 Run 输入、注册快照或图配置。

## 任务提交流程

1. API 从认证 Principal 得到 tenant 和 actor；先执行 Agent、项目、会话授权。
2. 服务解析当前已发布 Agent 和模型模板，检查模型供应商版本、工具、Skill、工作流引用。
3. 计算请求摘要并按 `(tenant_id, actor_id, idempotency_key)` 实现幂等提交。
4. 在 `platform_runs` 创建 queued Run；`task_id` 使用 Run UUID，并持久化 Agent 与模型模板版本；`dispatch_ready=false` 时 Worker 不可领取。
5. 将 Agent、模型模板、模型供应商、工具、Skill、工作流的确切活动版本固定到不可变 Run 注册快照；版本若在校验期间变更则拒绝提交。
6. 仅在快照持久化且摘要匹配后设置 `dispatch_ready=true`，再返回 queued 任务；后台 Worker 通过 `FOR UPDATE SKIP LOCKED` 和租约领取任务，调用 `LangGraphRunExecutor` 执行。
7. Worker 写入输出、Usage、Trace ID 和终态；工作台列表/详情读取同一份持久化 Run 记录。

## 宿主运行时的唯一扩展点

平台不允许从用户提交的 JSON 动态导入 Python 代码，也不会从 Agent definition 执行任意函数。每个 Agent 的 LangGraph 节点和可信工具处理器必须由宿主代码注册。

部署方提供可信 `graph_factory(agent_definition, snapshot, task_record, runtime_provider)`。平台的 `RegistryLangGraphExecutorResolver` 会构建 `executor_resolver(record, worker_id)`，并强制执行以下规则：

- 根据 `record.tenant_id` 解析租户级 Registry / 生命周期服务；
- 从 `RunRegistrySnapshotService.get(tenant_id, run_id)` 读取固定版本，不能重新读取“最新活动版本”；
- 从快照中取 `kind=agent` 的 definition，交给宿主的可信 Agent Graph Factory 构建 LangGraph；
- 使用 PostgreSQL 持久化 Checkpointer 编译图；
- 将同一 PostgreSQL `RunLifecycleManager`、同一 `PostgresRunStateStore`、传入的 `worker_id` 交给 `LangGraphRunExecutor`；
- 配置 `registry_snapshot_service`、可信 `registry_runtime_provider`、Run 记录中的 `template_id` 和 Agent definition 中的工具白名单。

示意：

```python
async def executor_resolver(record, worker_id):
    snapshot = await snapshot_service.get(
        tenant_id=record["tenant_id"], run_id=str(record["run_id"])
    )
    agent_entry = next(
        item for item in snapshot.entries
        if item["kind"] == "agent" and item["item_id"] == record["agent_id"]
    )
    graph = await trusted_agent_graph_factory(
        definition=agent_entry["definition"],
        snapshot=snapshot,
        task_record=record,
    )
    lifecycle = await lifecycle_resolver(record["tenant_id"])
    return LangGraphRunExecutor(
        graph=graph,
        lifecycle=lifecycle,
        worker_id=worker_id,
        registry_snapshot_service=snapshot_service,
        registry_runtime_provider=runtime_provider_for(record["tenant_id"]),
        model_template_id=record["template_id"],
        required_tools=tuple(agent_entry["definition"].get("tool_ids", ())),
    )
```

Worker 会验证返回对象必须是 `LangGraphRunExecutor`，且使用领取任务的同一 worker ID 和同一 RunStateStore。宿主 Graph Factory 是运行时扩展点，不是第二套任务/Run 生命周期。

宿主可直接使用平台的快照执行器解析器，而不是自行重复实现 Run 解析与执行器组装：

```python
executor_resolver = RegistryLangGraphExecutorResolver(
    snapshots=snapshot_service,
    lifecycle_resolver=lifecycle_for_tenant,
    runtime_provider_resolver=runtime_provider_for_tenant,
    graph_factory=trusted_agent_graph_factory,
)
task_worker = RunExecutionWorker(
    run_store=postgres_run_store,
    executor_resolver=executor_resolver,
    lifecycle_resolver=lifecycle_for_tenant,
    worker_id=stable_worker_id,
)
```

## 应用组装

- 使用同一连接池构建 `PostgresRunStateStore`、`PostgresRegistryRepository` 和 `PostgresRunSnapshotStore`。
- 使用 `create_registry_backed_task_service_resolver(...)` 组装工作台任务服务。
- 使用 `RunExecutionWorker(run_store=..., executor_resolver=..., lifecycle_resolver=..., worker_id=...)` 构建 Worker。
- 将 `task_service_resolver`、`task_authorizer` 和 `task_worker` 一并传给 `create_platform_app`。
- Worker 使用租约与 `SKIP LOCKED`，允许多实例竞争领取；部署时应为每个 Worker 实例配置唯一稳定 ID。
- 应用启动/关闭事件负责启动和停止 Worker。Worker 短暂数据库故障会继续轮询，不会把 queued 任务误报成功。

## 取消与恢复

运行中的跨进程取消先持久化为 `cancelling`。Worker 的租约心跳检测到该状态后取消当前图任务，再将 Run 转为 `cancelled`；API 不会在远端执行实际停止前提前报告取消完成。Worker 宕机时，租约过期后由其他 Worker 领取；有检查点的 Run 从检查点恢复，首次执行前即中断的 Run 可从已持久化初始输入重启；缺少必要检查点的中断 Run 失败关闭。人工审批暂停的 Run 不会被自动恢复。

## 数据库迁移

部署前应用 `agent_platform/storage/postgres/031_workbench_task_execution.sql`。该迁移保留旧 Run 的 Agent/模型字段作为兼容元数据，但新 Run 的权威依赖由不可变注册快照提供，因此移除新旧 Run 对旧 `platform_agents` / `platform_model_templates` 表的外键依赖；不复制或双写两套 Agent/模型定义。

## 尚不能由仓库自动完成的验证

- 宿主实际 Agent Graph Factory 的节点/工具绑定正确性；
- 真实模型代理、密钥解析、Gateway 工具调用与审批流程；
- 多实例部署的租约竞争、进程终止恢复和真实 PostgreSQL Checkpointer；
- 真实 UI 的提交、轮询、结果展示和跨进程取消。

这些依赖宿主部署配置或真实凭据，不以 FakeGraph 单元测试替代。


## 受监督委派的持久化 Child Run

`TaskCenterChildRunExecutor`（`agent_platform/runtime/delegation_task_center.py`）将 `ChildRunExecutor` 契约接入现有 Registry-backed Task Center，而不是直接在父 Run 内调用模型：

- 子 Run 使用委派契约生成的稳定 UUID；`submit_task(run_id=...)` 在 PostgreSQL 中创建 queued Run，任务输入、租户、Agent/模型模板版本快照、幂等键均持久化。
- 幂等键先做 SHA-256 摘要，避免用户可控 delegation key 触及 Task Center 的长度上限；同一父 Run 重试解析到同一 child Run。
- 创建前检查子 Agent 的活动模型模板 ID/版本与父 Run 固定模板一致，并确保委派工具是 Agent 已发布工具白名单的子集。
- 通过共享 `RunExecutionWorker` 的 `run_once()` 驱动持久化队列，等待子 Run 成功/失败/取消终态；输出、Usage 和 Trace ID 由现有 Worker 写回 PostgreSQL。
- 父 Run 超时或取消会取消等待协程，并向子 Run 提交持久化取消请求；Worker 确认执行停止后才进入 cancelled。
- 生产装配必须注入同一 PostgreSQL `RunStateStore` 的 Task Center 与 Worker，并提供从父 Run 可信元数据解析 actor 的 `actor_resolver`。不要从用户输入接受 actor ID。为解决 Graph Factory → Delegation Executor → Worker 的组合循环，可先创建不绑定 Worker 的 executor，创建 Worker 后调用 `bind_worker(worker)`。
- 宿主 Agent Graph Factory 必须将委派输入中的 `allowed_tools` 继续约束到 Gateway 调用；平台在提交前校验它是已发布 Agent 工具集合的子集，但不会替宿主动态生成任意工具执行代码。

该适配器提供可复用的持久化接线，不替代真实 PostgreSQL Checkpointer、Worker 多实例、模型代理和 Gateway 安全边界的部署演练。


## Run 生命周期 API 与操作者事件记录（L2-T17）

Run API 位于 `/api/platform/v1/runs/{run_id}`，取消、暂停和恢复操作在完成生命周期状态转换后追加操作者审计事件：

- `POST /{run_id}/cancel`：授权动作 `cancel`；事件 `human.run_cancel_requested`。
- `POST /{run_id}/pause`：授权动作 `pause`；事件 `human.run_paused`。
- `POST /{run_id}/resume`：授权动作 `resume`；事件 `human.run_resumed`，并在恢复前检查未决人工决策/审批。
- 操作者事件写入 `platform_run_events`，包含 Principal 的 actor_id、Run 状态及版本；服务端按 tenant_id + run_id 校验目标 Run，并对 payload 做脱敏。
- PostgreSQL RunStateStore 的状态转换仍在同一事务内追加 `run.status_changed`，记录 from_status、to_status 和版本；操作者事件补充“谁发起了动作”，不替代状态事实事件。
- `GET /{run_id}/events` 读取持久化事件；`GET /{run_id}/stream` 提供带 Last-Event-ID 恢复游标的 SSE 事件流。读写均先执行 Run 资源级授权。
- 如果宿主未注入 InterventionService，状态转换事件仍由 PostgreSQL 状态存储负责；actor 级动作事件依赖该服务的 record_action 能力。
