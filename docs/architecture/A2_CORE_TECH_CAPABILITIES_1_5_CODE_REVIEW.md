# A2：十项核心技术能力（1–5）代码映射审查

> Issue：[#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)  
> 本轮范围：第八节「十项核心技术能力检查清单」中的 1–5 项；不审查 6–10 项。  
> 基线：`main` @ `b622d5fc9eab1c79c28dcaa13367eec3c138679d`（2026-11-21）  
> 方法：逐项读取当前 main 的关键服务、领域契约、API、适配器和测试源码；将代码机制与 Issue 验收目标逐条对照。  
> 限制：未进行本地完整 checkout；未连接真实 PostgreSQL、Redis、Docker/Podman、外部 SaaS 或企业系统。前端页面/API 端到端联调未执行；没有证据的页面能力均标为未验证。

## 1. 结论摘要

五项能力均有真实代码基础，不属于纯设计；但成熟度不同，不能据此宣称五项均已完成：

| 能力 | 成熟度 | 代码基础 | 核心差距 |
|---|---|---|---|
| 1. 多智能体协同执行引擎 | 基础已完成 | LangGraph Send 并行专家、共享状态、汇总、冲突处理、人工 interrupt、可注入 checkpointer | 自动规划质量/分配策略、生产 checkpointer 装配、委派授权边界、端到端人工恢复和验收未证实 |
| 2. 任务执行与工作台引擎 | 基础已完成 | Run 状态机、PostgreSQL 持久化、租约/恢复、Session/Blackboard/Artifact 相关基础 | 前端工作台闭环、Task/Session/Run/Artifact 完整关联、产物版本交付、业务副作用恢复一致性未证实 |
| 3. 企业系统集成与连接器 | 部分实现 | Provider-neutral Connector 生命周期、secret_ref、游标 CAS、撤销清理；PostgreSQL/MySQL/S3/飞书/Notion/HTTP 六种 Provider | ERP/CRM/OA/HR/MES/PLM 等专用适配缺失；连接器开发者 SDK/测试发布标准、健康监控和生产接线未形成全面证据 |
| 4. 工具安全执行与沙箱 | 基础已完成 | SupervisedToolGateway、Schema 校验、服务端资源授权、审批消费、审计、幂等账本；OCI 安全配置 | OCI 实际运行隔离与宿主加固未真机验证；工具与沙箱强制接线、短期凭据全链路、紧急停止/结果对账闭环需验收 |
| 5. 事件驱动与可靠任务调度 | 基础已完成 | PostgreSQL Outbox、Redis Streams、lease、pending reclaim、DLQ/replay、PostgreSQL scheduler | 通用消费者幂等取决于 handler；定时调度与事件总线边界并存；补偿/优先级/容量公平性/全链路恢复证据不足 |

**总体判断**：应保留现有 LangGraph、Run lifecycle、Connector、Tool Gateway/OCI、Outbox/Redis Streams 和 Scheduler 作为演进底座。当前主要问题是跨模块契约和生产闭环，而不是缺少所有基础模块。不要重写五套新引擎。

## 2. 逐项代码映射

### A2-01 多智能体协同执行引擎

**目标逐项核对（Issue #848 第八节 1）**

| 目标子能力 | 当前证据 | 判断 |
|---|---|---|
| 多专家 Agent 并行执行 | `agent_platform/workflows/multi_expert.py` 使用 LangGraph `Send` 对每个 assignment 分派 `run_expert` 分支；`contributions` 使用 additive reducer | 已有实现 |
| 复杂任务自动拆解、规划和分配 | `MultiExpertServices.plan_experts()` 由注入服务返回专家分配；编排层不包含通用 planner 算法/任务 DAG 约束 | 部分实现，规划策略依赖外部服务 |
| Agent 间共享任务上下文、Blackboard 和中间结果 | `MultiExpertState` 含 tenant/task/run/context/assignments/contributions；`RunContext`、`BlackboardEntry/Event` 在 `agent_platform/contracts.py` 定义；PG 集成测试导入 `PostgresBlackboardRepository` | 有数据契约和共享状态基础；跨专家 Blackboard 持久化/读写调用需核对生产服务装配 |
| 结果汇总、质量校验、冲突检测与处理 | `synthesize`、`review`、`resolve_conflicts` 节点；`ordered_expert_contributions()` 按 expert/entry 稳定排序，避免并行完成顺序决定优先级 | 基础已实现；质量标准/评分阈值与客观验收策略依赖注入服务 |
| 人工介入后暂停、等待、恢复、重新分配 | `human_approval` 使用 LangGraph `interrupt()`；图支持 caller-supplied checkpointer；Run lifecycle 有 PAUSED/INTERRUPTED 与 checkpoint 状态 | 中断与恢复机制有基础；生产持久化 checkpointer、重启后恢复、重新分配策略未端到端验证 |
| 受监督 Agent 委派，明确范围、权限、责任和验收 | `tenant_id/task_id/run_id` 随分支 payload 传播；Gateway adapter 有 trusted identity/allowlist 机制 | 传播上下文不等于委派授权。委派主体、被委派 Agent 权限上限、责任链和结果验收需统一契约 |

**关键代码**

- `agent_platform/workflows/multi_expert.py`：`MultiExpertState`、`MultiExpertServices`、`expert_dispatch_payloads`、`ordered_expert_contributions`、`build_multi_expert_workflow`。
- `agent_platform/runtime/langgraph_adapter.py`：LangGraph Tool Gateway-only node、trusted runtime identity、tool allowlist。
- `agent_platform/contracts.py`：`RunContext`、`BlackboardEntry`、`BlackboardEvent`。
- `agent_platform/blackboard/postgres_repository.py` 与 `agent_platform/blackboard/protocol.py`：PG Blackboard repository 与版本冲突契约；本轮通过集成测试导入确认。
- `tests/test_platform_postgres_integration.py`：含 Blackboard PG repository/VersionConflict 的真实数据库集成测试代码；本轮未运行。

**缺口与影响**

1. `plan_experts` 是注入协议，编排器本身不保证任务拆解正确、专家能力匹配、负载均衡或失败重派。
2. LangGraph state 中的 contributions 是分支返回数据；共享 Blackboard 的写入、权限校验、版本冲突与并发合并由 services 实现，当前需进一步做生产调用链核验。
3. 图允许注入 checkpointer，但“允许传入”不等于生产使用 PostgreSQL 持久化 checkpointer。多专家分支、Blackboard、interrupt/resume 在进程重启后的端到端证据不足。
4. tenant/task/run 字段传播不是完整委派安全模型；缺少可验证的 delegation grant（被委派 Agent、工具范围、数据范围、额度、期限、撤销和责任归属）契约。
5. 冲突检测/质量审查通过 review service 注入；没有足够证据证明不同专家结论具备事实引用、质量阈值、失败回退和可审计的最终裁决。

**建议**：保留当前 LangGraph workflow。优先定义 DelegationContext（delegator/delegate/tenant/task/run/allowed tools/data scope/budget/expiry/revocation）、持久化 Blackboard checkpoint 以及专家质量评分契约；不要再造第二套并行 Agent 框架。

### A2-02 任务执行与工作台引擎

**目标逐项核对（Issue #848 第八节 2）**

| 目标子能力 | 当前证据 | 判断 |
|---|---|---|
| 任务生命周期、状态机、优先级、进度和执行记录 | `agent_platform/runtime/lifecycle.py` 定义 RunStatus、合法状态转换、RunSnapshot、RunStateStore；`runtime/postgres_lifecycle.py` 有 PG store/lease/恢复（A1 已读取） | Run 执行状态机基础已完成；业务 Task 与 Run 分层及统一进度事件待补 |
| 多轮会话持久化，Session 与 Task 关联 | PG 集成测试导入 `PostgresSessionRepository`；`RunContext` 含 task_id；本轮未取得 Session↔Task↔Run 完整关系/API/前端页面链路证据 | 部分实现/待验证 |
| 任务中断、取消、人工干预、断点恢复 | Run lifecycle 有 pause/cancel/interrupted/checkpoint；multi-expert graph 有 interrupt；Run store 有 recoverable 查询 | 基础已实现；真实多节点执行与 worker 重启恢复未运行验证 |
| 任务产物管理、版本、下载、追溯和交付 | PG 集成测试导入 `SessionAssetService`，使用 `InMemoryObjectStorage` 测试适配器；Blackboard EntryKind 含 ARTIFACT | 有产物/附件基础；对象存储生产实现、产物版本/权限/下载/交付闭环需补证 |
| 长任务恢复后避免重复执行已完成业务动作 | `agent_platform/tools/idempotency.py` 提供 durable ToolEffect ledger：claim/complete/fail/reconcile；已声明 ambiguous failure 不自动重试 | 工具副作用有强幂等基础；所有业务动作必须经过该 ledger 的覆盖范围需验证 |

**关键代码**

- `agent_platform/runtime/lifecycle.py`：RunStatus、RunSnapshot、RunStateStore、取消传播和工具调用跟踪。
- `agent_platform/runtime/postgres_lifecycle.py`：PostgreSQL Run state、乐观版本、worker lease、recoverable Run。
- `agent_platform/contracts.py`：RunContext、Blackboard/Artifact 类型。
- `agent_platform/tools/idempotency.py`：ToolEffect、claim、complete、fail、reconcile。
- `agent_platform/api/scheduler.py`：调度 API 提供作业与运行记录；它不是完整任务执行工作台 API。
- `tests/test_platform_postgres_integration.py`：PG Session、Blackboard、Connector、Outbox、Registry 等集成测试代码；本轮未运行。

**缺口与影响**

1. 需明确 Task（业务目标/责任/状态）与 Run（执行实例）的关系；同一 Task 多次 Run、重试、人工重派、并行子 Run 的状态归并规则尚未形成完整统一证据。
2. 任务进度不能只依赖 Run 状态；需统一 Step/Activity/ToolCall/Approval/Event 的进度事件，避免工作台展示与实际执行脱节。
3. Session 与 Task 关联、会话消息上下文恢复、任务协作成员/可见性和多轮续办的完整 API 与 UI 联动本轮未验证。
4. SessionAssetService 的测试导入 InMemoryObjectStorage，不能证明生产对象存储版本、下载授权、病毒扫描/内容安全、保留/删除传播和跨租户隔离已验收。
5. ToolEffect ledger 可避免不确定失败后盲目重放，但必须确保生产 Tool Gateway 所有外部副作用都统一调用；人工 reconciliation 的授权与审计接线需核验。

**建议**：将 Task 作为业务工作项、Run 作为执行尝试、Artifact 作为不可变交付物、Session 作为协作上下文统一关联；补齐任务工作台真实 API/UI 和进度事件契约。保留现有 Run lifecycle 与 ToolEffect ledger。

### A2-03 企业系统集成与连接器

**目标逐项核对（Issue #848 第八节 3）**

| 目标子能力 | 当前证据 | 判断 |
|---|---|---|
| 外部工具、MCP、Webhook、数据库、API、消息系统接入 | `agent_platform/tools/gateway.py`、`runtime/langgraph_adapter.py`、`agent_platform/events/webhooks.py`、`control_plane/data_connectors.py`、`connectors/providers.py` | 接入框架基础已具备 |
| 连接器配置、授权、凭据引用、状态监控、健康检查和数据同步 | DataConnection 含 tenant/provider/secret_ref/scopes/status/cursor/version/last_synced_at/revoked_at/last_error；SecretResolver 运行时解析；cursor CAS；cleanup queue | 生命周期和凭据引用基础已实现；主动健康检查/连接诊断覆盖未证实 |
| 统一工具调用网关及 Connector 开发、测试、发布规范 | SupervisedToolGateway 和 provider-neutral Protocol 存在 | Gateway 已有；开发 SDK、兼容性/契约测试套件、发布/签名规范未见完整证据 |
| ERP/CRM/OA/HR/MES/PLM/财务/企业微信/钉钉/飞书适配 | Provider labels 明确为 PostgreSQL、MySQL、S3、飞书、Notion、HTTP JSON | 已实现六种通用 Provider；不能据此宣称 ERP/CRM/OA 等业务系统专用适配完成 |
| 区分已实现与计划适配系统 | PROVIDER_LABELS 明确 Provider 类型 | 可明确已实现范围；具体系统对象/授权流程/增量同步质量仍需逐 Provider 验收 |

**关键代码**

- `agent_platform/control_plane/data_connectors.py`：`DataConnection`、`ConnectorProvider`、`ConnectorRepository`、`ConnectorCleanupQueue`、`ConnectorSink`、`SecretResolver`。
- `agent_platform/connectors/providers.py`：`PROVIDER_LABELS` 与 PostgreSQL/MySQL/S3/Feishu/Notion/HTTP Provider。
- `agent_platform/control_plane/postgres_connectors.py`：PG Connector repository（由 PG integration test 导入确认）。
- `agent_platform/tools/gateway.py`：统一受监督工具执行入口。
- `agent_platform/events/webhooks.py`、`webhook_worker.py`：Webhook 注册/投递链路。
- `agent_platform/scheduling/service.py`：connector_sync 内置调度作业；`connector_sync:<连接ID>` 支持单连接调度。
- `tests/test_platform_postgres_integration.py`：Connector repository 集成测试代码；本轮未运行。

**缺口与影响**

1. 六类 Provider 是通用数据接入适配，不代表 ERP/CRM/OA/HR/MES/PLM 的领域对象、分页/增量、Webhook 回调、权限委托、限流和错误恢复已实现。
2. DataConnection.secret_ref 只保证领域模型保存引用；凭据加密、轮换、最小 scope、短期访问 token、日志脱敏和凭据撤销后的数据清理仍需逐生产 Provider 验收。
3. Cursor compare-and-swap 可防止并发覆盖，但端到端数据同步仍需核验重复页、游标过期、源端删除、部分失败、限流退避和 sink 幂等。
4. ConnectorCleanupQueue 已有 remote revoke/sink cleanup 的状态模型，但删除数据的保留策略、重试/死信告警和多租户隔离需运行验证。
5. Scheduler 内置 connector_sync 入口并不等于所有连接器均有 UI 状态、手动同步、失败诊断和数据质量报告。

**建议**：保留 provider-neutral ConnectorService/Provider Protocol。优先建立 Connector SDK + contract test harness，先完成通用 OAuth/secret lifecycle、健康探测、增量游标/删除语义、限流和审计，再按客户真实需求逐个增加企业系统适配器。不要把“支持 HTTP JSON”包装成“支持所有企业系统”。

### A2-04 工具安全执行与沙箱

**目标逐项核对（Issue #848 第八节 4）**

| 目标子能力 | 当前证据 | 判断 |
|---|---|---|
| 工具白名单、JSON Schema 参数校验、服务端权限检查 | `SupervisedToolGateway` 要求唯一工具名、policy_version、resource_resolver/resource_authorizer；配置 JSON Schema 时使用 Draft 2020-12 validator；LangGraph Gateway node 有 allowlist | 基础已完成 |
| 执行身份绑定、短期 Token、凭据托管、最小权限 | ToolExecutionContext 含 tenant/actor/roles/run/agent/tool/resource；Gateway identity 从可信服务端 resolver 解析；Connector SecretResolver 延迟解析 | 身份上下文基础已具备；短期凭据发放/撤销在每种工具适配器中的一致性未证实 |
| OCI/容器隔离与生命周期 | `agent_platform/sandbox/oci.py`：digest pin、Docker userns-remap 验证、Podman/Docker、read-only root、无网络/无 host mount、capability drop、memory/CPU/PIDs/tmpfs/timeout/output 限制 | 安全配置实现明确；宿主/daemon 真实隔离未运行验证 |
| 文件系统、网络出口、CPU、内存、时间、并发限制 | OciSandboxConfig 对镜像 digest、memory/cpu/pids/tmpfs/timeout/output/stdin/env allowlist 做校验；模块说明 no network/no host mounts/read-only | 基础实现；并发容量/宿主网络强制策略仍待部署验收 |
| 高风险审批、二次确认、紧急终止、结果核对和审计 | `approval/policy.py`、`tools/gateway.py`、`tools/idempotency.py`、`approval/postgres.py`；风险等级、审批消费、审计和副作用对账基础存在 | 审批/审计/对账基础已具备；统一生产路径和紧急停止闭环待验证 |

**关键代码**

- `agent_platform/tools/gateway.py`：`ToolExecutionContext`、`ToolDefinition`、`SupervisedToolGateway`；构造时强制每个 Tool 声明可信资源解析器和授权器。
- `agent_platform/runtime/langgraph_adapter.py`：`GatewayToolClient`、`GatewayToolNode`；拒绝未 allowlist 工具，拒绝缺失或跨 tenant 身份。
- `agent_platform/sandbox/oci.py`：`OciSandboxConfig`、`OciSandbox`（沙箱执行）。
- `agent_platform/approval/policy.py`、`approval/postgres.py`：风险策略、审批摘要与原子消费。
- `agent_platform/tools/idempotency.py`：副作用 ledger 和人工 reconciliation。

**缺口与影响**

1. Gateway 的存在不能证明所有 Agent/Harness/Connector 的副作用路径都被强制经过 Gateway；需静态调用图 + 负向集成测试证明无旁路。
2. OCI 配置限制只对通过该执行器启动的 workload 生效。宿主 Docker/Podman daemon 权限、rootless/userns、seccomp/AppArmor、磁盘/网络策略和镜像供应链需在部署环境验收。
3. 模块注释描述 no network/no host mounts，仍应对最终实际 docker/podman argv 做断言测试，防止未来改动意外放宽。
4. 环境变量 allowlist 拒绝 secret/token/password/database/socket 等敏感键是有价值的 fail-closed 约束；但凭据必须通过独立短期凭据通道提供，不可为了兼容把 secret 放回 env。
5. 紧急停止需要同时取消 Run、停止 sandbox process、撤销工具凭据、阻断后续调用并记录终止原因；仅 Run 状态进入 CANCELLED 不足以证明容器已终止。
6. 工具结果核对与补偿依赖 ToolEffect reconciliation；必须确认生产 resolver、审批 verifier、audit sink 均真实装配，缺失依赖应 fail closed 而非造成不透明 500。

**建议**：保留 SupervisedToolGateway 与 OCI sandbox。建立统一 SandboxExecutionRequest（tenant/run/actor/tool/image digest/resource profile/credential lease/deadline/correlation id），并以 Release Gate 验证 Gateway 强制接线、rootless daemon、网络隔离、凭据撤销和紧急停止。

### A2-05 事件驱动与可靠任务调度

**目标逐项核对（Issue #848 第八节 5）**

| 目标子能力 | 当前证据 | 判断 |
|---|---|---|
| 事件总线、异步分发、定时/业务事件触发 | `PostgresOutboxStore` + `RedisStreamsTransport`；`PostgresSchedulerService` 提供定时作业；Webhook worker 处理推送 | 基础已完成 |
| 消息确认、Pending 恢复、消费幂等、去重 | Redis Streams group/read/ack/xautoclaim；event_id UUID；PG EventReceipt 在 integration test imports；Outbox at-least-once | Pending 恢复和去重存储有基础；通用业务副作用 exactly-once 不成立 |
| 失败重试、指数退避、DLQ、重放、回执 | Outbox lease/attempts/next_attempt_at/dead_lettered_at；Redis dead-letter；Outbox replay bounded window；Webhook delivery/replay | 基础已完成 |
| 超时、取消、补偿、优先级、并发和容量控制 | Run lifecycle 有 timeout/cancel/lease；Scheduler 对同一 job 使用行锁避免并发；Outbox SKIP LOCKED；ToolEffect reconciliation | 分散机制已存在；统一优先级、公平调度、租户容量配额和补偿编排未证明 |

**关键代码**

- `agent_platform/events/outbox.py`：`PostgresOutboxStore.claim_batch` 以 `FOR UPDATE SKIP LOCKED` 领取，lease token/attempts/next_attempt_at/dead-letter；`requeue_published` 要求显式确认和时间窗。
- `agent_platform/events/redis_streams.py`：Redis Streams transport，consumer group、read_group、claim_pending（`XAUTOCLAIM`）、ack、dead-letter；publish 以 event_id UUID。
- `agent_platform/events/outbox_worker.py`：OutboxRelayWorker 周期发布，连接/停止 transport。
- `agent_platform/scheduling/service.py`：`PostgresSchedulerService`，作业定义/执行历史 PostgreSQL 持久化；同一 job 使用 `SELECT ... FOR UPDATE`，connector_sync 作业。
- `agent_platform/api/scheduler.py`：/api/platform/v1/scheduler/jobs、runs、subscriptions 等 API，身份来自 request.state Principal 并限定 tenant。
- `agent_platform/tools/idempotency.py`：外部副作用账本和 reconciliation。
- `tests/test_redis_streams_integration.py`：真实 Redis 集成测试，需 `AGENT_PLATFORM_TEST_REDIS_URL`；`tests/test_platform_postgres_integration.py`：真实 PostgreSQL 集成测试，需 `AGENT_PLATFORM_TEST_DSN`。

**缺口与影响**

1. Outbox + Redis Streams 是 at-least-once，不是通用 exactly-once。Redis 发布成功而 Outbox receipt/状态确认前进程崩溃时可能重复投递；消费者必须持久化 event_id receipt。
2. EventReceipt 去重只能阻止重复消费处理；外部副作用成功但 receipt 尚未提交的窗口仍需业务幂等键/ToolEffect ledger 和 reconciliation。
3. Scheduler 是 PostgreSQL 单集群轮询作业服务，和 Outbox/Streams 是并列机制；需要统一 trigger contract、任务执行入口、追踪 ID 和重试策略，避免多个调度系统职责重叠。
4. Scheduler stale running 以 10 分钟后复位；需验证长任务租约/心跳与恢复逻辑是否可能误判活跃任务。
5. Scheduler 通过 job row lock 防止同一作业重复执行，但没有足够证据表明跨租户公平性、全局并发容量、优先级队列和资源隔离已完成。
6. Outbox claim 依据 created_at 排序；未见统一业务优先级调度/加权公平队列实现。
7. 集成测试只有在 Redis/PG 环境变量配置时运行；未配置时 skip。仓库有测试源码不等于当前 CI 实际执行了真实中间件测试。

**建议**：保留 PG Outbox + Redis Streams 和 PostgreSQL Scheduler。统一 EventEnvelope/Trigger/RunSubmission 契约；以 event_id receipt + ToolEffect key + reconciliation 构成副作用可靠性闭环；补齐 scheduler 租约心跳、优先级、公平性和容量限额。

## 3. 横向依赖与架构边界

建议五项能力沿统一调用链协作，不应重复建设：

1. Multi-agent planner 产生受约束的 Task/Delegation；每个子 Agent 继承受限身份和资源范围。
2. Task 管理业务目标与责任；Run lifecycle 管具体执行尝试；Session 管协作上下文；Artifact 管交付结果。
3. Connector Provider 负责协议适配；SecretResolver 只在执行时解析凭据；Tool Gateway 负责所有会产生副作用的调用。
4. OCI sandbox 负责不可信代码隔离；Gateway/Policy/Approval 负责业务动作授权，两者不可互相替代。
5. Scheduler 产生 Trigger；Outbox/Streams 可靠传递事件；Run Engine 执行任务；ToolEffect ledger 处理副作用幂等和人工对账。

主要边界断点：Delegation 授权与责任链、Task/Session/Artifact 产品闭环、Connector 健康诊断与系统专用适配、Sandbox 实际宿主隔离、Scheduler 与 Event Bus 的统一触发/容量治理。

## 4. 去重后的研发缺口 Backlog

| ID | 优先级 | 缺口 | 依赖 | 可验证验收标准 |
|---|---|---|---|---|
| A2-B01 | P0 | Gateway 强制覆盖所有工具/连接器副作用路径 | Runtime、Tool、Connector | 静态调用图与负向测试证明所有生产副作用必须经过 Gateway；无 Gateway 的执行被拒绝 |
| A2-B02 | P0 | 生产 OCI Sandbox 宿主隔离和紧急停止闭环 | Sandbox、Run、Credential | rootless/userns、seccomp/AppArmor、无网络/host mount、资源限制测试通过；Stop 后进程/凭据/后续调用全部终止 |
| A2-B03 | P1 | Agent 委派身份与权限范围模型 | Identity、Multi-agent、Policy | 子 Agent 不能提升父授权；范围/额度/期限/撤销/责任可审计；越权测试通过 |
| A2-B04 | P1 | 多专家图持久化 checkpoint 和人工恢复验收 | Multi-agent、Run、Blackboard | 多分支并发中断、进程重启、审批恢复、分支失败重派的 PG 集成测试通过 |
| A2-B05 | P1 | Task/Session/Run/Artifact 统一工作台契约 | Task、Session、Runtime、Storage | API/UI 可创建任务、持续会话、看执行步骤/审批/日志、断点恢复、下载版本化产物；权限负向测试通过 |
| A2-B06 | P1 | Connector SDK、健康探测和数据同步契约测试 | Connector、Secret、Scheduler | 每个 Provider 通过统一 contract tests：授权、轮换、分页、游标、限流、删除、撤销清理、tenant isolation |
| A2-B07 | P1 | 企业系统适配器路线按实际需求落地 | Connector、Partner ecosystem | 对每个目标系统明确官方 API、对象覆盖、授权方式、增量同步、Webhook 和验收；不把 generic HTTP 算成原生适配 |
| A2-B08 | P1 | Event 消费幂等和副作用对账统一封装 | Event、ToolEffect、Run | 重复消息、receipt 提交前崩溃、外部副作用超时等故障注入不重复产生业务效果 |
| A2-B09 | P2 | Scheduler 与 Event Bus 统一 Trigger/Capacity 语义 | Scheduler、Event、Task | 统一 Trigger schema、优先级、公平性、租户并发配额、租约心跳、取消/补偿和可观测指标 |
| A2-B10 | P2 | 多 Agent 质量评测与专家分配反馈闭环 | Multi-agent、Evaluation、Observability | 固定数据集可复现比较规划/专家分配/冲突解决质量、成本、延迟和失败率 |

## 5. 验证记录与边界

| 验证项 | 本轮结果 | 说明 |
|---|---|---|
| Issue 任务清单 | 已核对 | 对照 Issue #848 第八节 1–5 项逐条映射 |
| main 基线 | 已确认 | `b622d5fc9eab1c79c28dcaa13367eec3c138679d` |
| 关键源码 | 已读取 | multi_expert、contracts、LangGraph adapter、Run lifecycle、Scheduler API/service、Connector provider/service、Tool Gateway、OCI sandbox、Outbox、Redis Streams、Outbox worker、ToolEffect ledger |
| 测试源码 | 已读取 | `tests/test_platform_postgres_integration.py`、`tests/test_redis_streams_integration.py`；均为真实中间件集成测试入口 |
| 测试环境要求 | 已确认 | PostgreSQL 需 `AGENT_PLATFORM_TEST_DSN`；Redis 需 `AGENT_PLATFORM_TEST_REDIS_URL`；未配置时测试 skip |
| 本地自测 | 未运行 | 当前环境没有完整仓库 checkout |
| PostgreSQL/Redis/OCI 真机验证 | 按要求跳过 | 未连接数据库、Redis、Docker/Podman、外部 SaaS 或企业系统 |
| 前端工作台联调 | 未验证 | 本轮未获得可复现的前端页面→API→后端完整链路证据，不据此判定已完成 |
| CI | PR 创建后核对 | 以该 PR commit 的 GitHub Actions/combined status 为准；未运行/无记录则标 pending |

### 可复现测试建议（不代表本轮已运行）

- `pytest -q tests/test_platform_postgres_integration.py`（需 `AGENT_PLATFORM_TEST_DSN`）
- `pytest -q tests/test_redis_streams_integration.py`（需 `AGENT_PLATFORM_TEST_REDIS_URL`）
- `pytest -q tests/test_platform_postgres_integration.py -k 'blackboard or connector or session or outbox or scheduler'`
- 对 `multi_expert` 增加/执行并行分支、冲突、interrupt/resume、进程重启的 workflow integration tests。
- 对 `sandbox/oci.py` 执行 argv contract tests 与隔离负向测试（宿主真实运行按后续受控环境执行）。

## 6. 审查边界

- 本报告只覆盖 Issue #848 第八节十项核心技术能力中的 1–5 项，不覆盖 6–10 项，也不代表其他编号任务完成。
- 只新增审查文档，不修改产品业务代码、数据库迁移、运行时行为或前端页面。
- 不申请真实服务器/外部资源；不连接真实 PostgreSQL、Redis、Docker/Podman、外部 SaaS。
- 未执行的测试明确标记为未运行；测试文件存在不等于 CI 已运行或验收通过。
- 本轮 PR 不合并，Issue #848 保持原状态。
