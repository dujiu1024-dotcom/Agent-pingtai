# A1：八大核心引擎代码映射审查

> Issue：[#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)  
> 审查范围：仅限身份与组织、AI 资产、任务与调度、事件驱动、策略审批、统一执行运行时、数据知识、观测计量八大引擎。  
> 基线：`main` @ `b622d5fc9eab1c79c28dcaa13367eec3c138679d`（2026-11-21）  
> 方法：GitHub main 分支源码逐项读取、现有测试/CI 证据和 Issue #848 已有审查记录交叉核对。  
> 限制：本轮不连接 PostgreSQL/Redis/MinIO/模型服务，不申请真实服务器；本环境不能进行本地完整 checkout，因此不把静态代码检查描述为运行验收。

## 1. 结论摘要

当前仓库已有可复用的模块化平台底座，八大引擎并非从零开始。身份、任务生命周期、事件 Outbox、审批、知识检索、Trace/指标等均存在真实实现；但“模块存在”不等于“统一引擎已经端到端闭环”。主要缺口集中在：

- 统一组织资源模型尚未证明覆盖企业—部门—团队—用户—数字员工全层级；数字员工独立身份/岗位/责任模型证据不足。
- AI 资产注册支持多类版本化定义，但资产类型覆盖、发布审批、环境晋级、依赖锁定、灰度/回滚和生态分发没有形成统一闭环。
- Run 生命周期与任务/调度、Worker 执行身份、外部副作用对账仍有边界断点。
- Outbox/Redis Streams 是至少一次投递；业务副作用去重依赖消费者实现，不能宣称通用 exactly-once。
- 策略/审批有 fail-closed 风险分类和动作摘要绑定基础，但默认生产执行路径的统一 Gateway/Policy/Approval 接线仍需补齐。
- 知识库具备 tenant/ACL-first 检索接口；对象存储、异步任务、Connector、索引更新/删除等路径仍需完整隔离证据。
- 观测模块有 Trace、Metrics、Evaluation、Quota/成本线索；企业/部门/团队成本归集、SLA、业务价值和生产评测门禁未形成完整验收证据。

**成熟度总览**

| 引擎 | 当前成熟度 | 可复用基础 | 主要缺口 |
|---|---|---|---|
| 身份与组织 | 基础已完成 | Session/API Credential、Tenant membership、RBAC/resource scope | 组织层级继承、数字员工身份、全链路隔离 |
| AI 资产 | 部分实现 | Tenant-scoped Registry、版本、状态、secret reference 约束 | 全资产统一生命周期、发布治理和生态分发 |
| 任务与调度 | 基础已完成 | Run 状态机、PostgreSQL 持久化、lease、checkpoint/recovery、scheduler API | Task/Run 完整模型、Worker 授权复核、独立弹性、副作用幂等 |
| 事件驱动 | 基础已完成 | PostgreSQL Outbox、Redis Streams relay、lease、退避、DLQ/replay | 通用消费者幂等约束、事件目录/规则运营、故障演练 |
| 策略与审批 | 基础已完成 | 风险等级、默认拒绝、动作摘要、一次性审批消费、审计 | 生产组合根全链路强制接入、审批人资格/策略治理验收 |
| 统一执行运行时 | 基础已完成 | Runtime lifecycle、Harness/Workflow、Blackboard、Tool/MCP Gateway | 所有生产 Run 统一 Runtime Builder、Worker 身份、跨 Harness 真实一致性 |
| 数据与知识 | 基础已完成 | Connector、Knowledge、ACL 检索、RAG、Memory、Graph | 数据血缘/分级、删除传播、对象存储/异步链路 ACL、连接器适配深度 |
| 观测与计量 | 部分实现 | Trace/Span、低基数 Metrics、Evaluation、Quota/usage | 成本准确归集、SLA/SLO、质量门禁生产接线、ROI |

成熟度定义沿用 Issue #848：已完成必须有端到端运行和验收证据；基础已完成表示机制真实存在但闭环或验收不足；部分实现表示仅覆盖部分职责。静态读取未执行的测试均标为“存在但本轮未运行”。

## 2. 八大引擎逐项映射

### A1-01 身份与组织引擎

**代码映射**

- `agent_platform/identity/models.py`：`UserRecord`、`SessionRecord`、`Principal`、`ApiCredentialRecord`、`ResourceRef`、`AuthorizationContext`。
- `agent_platform/identity/service.py`：`IdentityService` 负责登录、会话认证/撤销、权限判定、角色授予/撤销、API Credential 生命周期、邀请。
- `agent_platform/identity/postgres.py`：`PostgresIdentityRepository`，查询限定 tenant、active tenant、active user 和 active membership；RBAC 查询和角色绑定持久化。
- `agent_platform/identity/protocols.py`：身份持久化与权限端口。
- `agent_platform/production.py`：生产组合根装配身份服务与相关 repository。
- `agent_platform/identity/`、`agent_platform/api/` 及 SQL migrations：身份 API、组织/成员/角色数据结构的进一步核验入口。

**已具备**

- 人类用户、Tenant membership、Session、API Credential、邀请和角色绑定有独立领域对象/持久化实现。
- Session/API Credential 校验包含撤销、过期、用户状态和 membership 状态检查。
- 授权查询以 tenant_id、user_id、resource_type、resource_id、permission_key 为边界；API Credential 同时校验 scope 与 RBAC。
- 已有机器身份/Agent 授权与 ResourceScopeAuthorizer 的专项审查记录；身份基础不只是前端角色菜单。

**缺失/风险**

1. 组织树（Organization/Department/Team）的层级、继承、跨部门委派和有效权限计算，尚缺统一资源关系图与完整迁移/API/测试证据。
2. DigitalEmployee 不能等同 Agent。当前已读取模型未显示独立数字员工组织身份、岗位职责、主管/负责人、责任主体、绩效和调岗生命周期。
3. Tenant 隔离的正向基础较完整，但后台 Worker、Outbox、Connector、对象存储、缓存和异步任务的主体传播/撤权时效仍未形成完整矩阵。
4. No-auth 模式存在多层旁路能力；默认生产组合根启用认证的证据已在既有审查中确认，但生产 Release Gate 仍应强制校验 auth enabled。
5. 首次初始化并发竞争已在此前审查中识别：公开初始化检查与创建不在同一原子单例保护下，需以数据库约束/锁和并发测试闭合。

**建议**：保留 IdentityService 与 Repository；优先统一组织节点/成员/角色/资源作用域契约，再把 DigitalEmployee 建成有负责人和权限边界的组织主体，而不是给 Agent 增加展示字段。

### A1-02 AI 资产引擎

**代码映射**

- `agent_platform/control_plane/registry.py`：`RegistryService`、`RegistryVersion`；支持 `agent`、`workflow`、`tool`、`skill`、`model_provider`、`model_template`、`routing_policy` 等 kind，状态包含 draft/published/disabled/deprecated。
- `agent_platform/control_plane/postgres.py`：`PostgresRegistryRepository`，按 tenant_id/kind/item_id/version 读取版本，拒绝 tenant 不匹配访问。
- `agent_platform/extensions/`：扩展发布者信任密钥注册/撤销；此前审查已确认 Extension Manager 有扩展包依赖、版本、签名信任、安装/升级/卸载生命周期。
- `agent_platform/production.py`：生产组装 Registry、Connector 和扩展发布者信任服务。

**已具备**

- Registry 元数据与可执行 handler 分离；资产定义不允许直接携带密钥明文，要求 secret reference。
- Registry 具备 tenant-scoped 版本化存储、并发冲突/不可变版本语义和发布状态。
- 扩展发布者公钥信任机制可复用为生态供应链基础。

**缺失/风险**

1. 当前 Registry kind 并非 Issue 资源清单的全量覆盖；Prompt、Connector、KnowledgeBase、Artifact 等资产是否纳入统一 Registry/版本依赖语义，需要逐项明确。
2. Agent/Workflow/Skill/Tool/Model/Prompt/Connector 缺少一套统一的“开发—测试—审批—预发布—生产—灰度—回滚—撤销”证据链。
3. 依赖锁定、兼容性声明、环境差异配置、资产签名/来源、漏洞扫描、升级影响分析和版本回滚策略未证明统一实现。
4. 扩展包发布生命周期不等于平台全部 AI 资产生命周期；也不等于 Marketplace、伙伴认证、企业私有/共享/公开可见性与商业授权已完成。

**建议**：保留 Registry 与 TrustedPublisherKeyRegistry；建立跨资产 kind 的统一生命周期契约，明确 Registry 元数据、Artifact 内容存储、Connector 凭据和 Extension 安装执行之间的边界。

### A1-03 任务与调度引擎

**代码映射**

- `agent_platform/runtime/lifecycle.py`：Run 状态转换、取消、暂停、恢复、超时、租约失效后的恢复；暂停要求 durable checkpoint。
- `agent_platform/runtime/postgres_lifecycle.py`：`PostgresRunStateStore`，Run 持久化、版本乐观锁、priority、idempotency_key、request_digest、registry_snapshot、worker lease。
- `agent_platform/api/scheduler.py`：`/api/platform/v1/scheduler/jobs` 查询/创建/更新定时作业和手动触发；从请求身份取得 tenant_id。
- `agent_platform/production.py`：装配 Run lifecycle、scheduler 和 worker 相关组件。
- 测试证据入口：`tests/test_platform_postgres_integration.py`、任务/Run lifecycle 与 scheduler 对应单元测试。

**已具备**

- Run 与 Task 有关联字段；执行快照保留 Agent/Model/Workflow 版本信息。
- Run 有 queued/running/paused/interrupted/cancelling/cancelled/failed/timed-out 等生命周期控制；有 checkpoint、lease 和乐观并发更新。
- scheduler API 已存在租户身份上下文；定时作业具备手动运行与启停管理入口。

**缺失/风险**

1. Task 与 Run 的领域边界、父子任务、跨 Agent 委派、Task 产物和 Approval/Event 的关系尚未统一成可追溯资源图。
2. RunExecutionWorker 与 FastAPI startup/shutdown 生命周期耦合；Compose 独立 platform-worker 当前主要承载 Outbox relay，不等同独立 Run Worker。API 扩容会连带扩大 Run Worker，弹性和资源隔离不足。
3. Worker 恢复任务时缺统一执行时主体身份/权限复核端口；不能仅凭创建时 created_by/session_id 推断当前仍有权限。
4. Run 重试、恢复与外部业务副作用之间需要稳定幂等键、效果回执、对账/补偿和人工接管闭环。
5. 调度策略的优先级、公平性、并发配额、租户级容量、超时与取消的竞态，需要跨多 Worker 故障测试证明。

**建议**：保留 RunLifecycle/PostgresRunStateStore；拆清 Task（业务任务）与 Run（执行实例），把 Worker 主体授权复核和副作用账本设为 P0，再独立部署 Run Worker。

### A1-04 事件驱动引擎

**代码映射**

- `agent_platform/events/outbox.py`：`PostgresOutboxStore`、`OutboxRelay`；`FOR UPDATE SKIP LOCKED`、lease token、失败退避、dead-letter、限定时间窗 replay。
- `agent_platform/events/redis_streams.py`：Redis Streams transport。
- `agent_platform/events/outbox_worker.py`：Outbox relay worker。
- `agent_platform/events/admin.py`、`webhooks.py`、`webhook_worker.py`、`webhook_replay.py`：Webhook subscription、delivery、dead-letter replay。
- `agent_platform/api/scheduler.py`：定时触发入口。

**已具备**

- PostgreSQL 作为事件意图事实源，Redis Streams 作为分发传输；租约、receipt/pending reclaim、退避、DLQ 和 checkpoint/replay 机制已有实现线索。
- Outbox relay 发布前写审计；Redis 发布成功但数据库确认失败时明确记录可能重复投递，要求消费者按 event_id 去重。
- Webhook 具备订阅、投递和死信重放服务。

**缺失/风险**

1. 当前语义是 at-least-once，不是通用 exactly-once；消费者外部副作用若未幂等，仍有“副作用成功、receipt 完成前崩溃”的重复窗口。
2. 事件 schema/version、领域事件目录、订阅权限、事件过滤/路由规则和运营可视化尚未证明统一治理。
3. 事件保留/归档、跨租户重放授权、重放速率限制、毒消息隔离和跨版本兼容需要补齐。
4. 定时调度与业务事件触发尚未形成统一 trigger contract 和完整可观测闭环。
5. Redis/多实例故障注入测试本轮未执行；不能将测试源码存在视为故障恢复验收通过。

**建议**：保留 Outbox + Streams；对 Consumer 增加强制 event_id 幂等注册/执行契约，外部副作用必须带业务幂等键及 reconciliation hook。

### A1-05 策略与审批引擎

**代码映射**

- `agent_platform/approval/policy.py`：`RiskLevel`、`ActionRequest.digest()`、`SupervisedPolicy.evaluate()`。
- `agent_platform/approval/service.py`：`ApprovalService` 和审批生命周期。
- `agent_platform/approval/postgres.py`：`PostgresApprovalVerifier.consume()` 按 tenant、approval、action_digest、policy_version、approved 状态和过期时间原子消费；同文件有审计 sink。
- `agent_platform/production.py`：审批服务、verifier、审计相关生产装配；`runtime`/Tool Gateway 为执行入口。

**已具备**

- 未注册工具默认拒绝；风险分类来自可信服务端工具注册表，而不是模型输出。
- L3 金融/安全高风险动作不能由 Agent 自动执行，要求授权人类直接执行。
- L2 外部/不可逆动作需要审批，审批摘要绑定 action/resource/actor/parameters，变更目标或参数会使审批失效。
- PostgreSQL verifier 以原子更新消费已批准审批，支持过期与策略版本校验。

**缺失/风险**

1. 策略规则的管理、版本发布、冲突检测、模拟评估和回滚尚未证明形成独立控制面产品。
2. 审批人资格/组织关系、双人复核、金额/范围/时间/次数限制及委托审批的统一规则仍需端到端核验。
3. 既有审查发现生产 ToolEffectReconciliationService resolver 曾注入空授权回调和空 audit_sink，可能导致拒绝/500；需在生产组装与真实服务测试中确保授权和审计依赖完整。
4. 统一 Runtime/Gateway/Policy/Approval 并非仅靠各模块存在即可成立；需要证明所有生产工具执行路径都不可绕过。
5. 审批撤销、权限撤销与排队中/恢复中 Run 的竞态处理需要补测试。

**建议**：以 Tool Gateway 为唯一副作用执行入口；把 Policy Decision、Approval consumption、Credential resolution、Audit intent/result 作为强制中间件/服务端端口，默认 fail closed。

### A1-06 统一执行运行时

**代码映射**

- `agent_platform/runtime/lifecycle.py`、`runtime/postgres_lifecycle.py`：Run 生命周期与持久化。
- `agent_platform/workflows/multi_expert.py`：多专家 LangGraph 工作流、并行执行、结果汇总/冲突处理/人工审批/Blackboard 交互（既有审查读取）。
- `agent_platform/contracts.py`、`agent_platform/blackboard/`：Scope、BlackboardEntry/Event、版本、状态、correlation_id 和 PostgreSQL repository。
- `agent_platform/sandbox/oci.py`：OCI 沙箱执行约束（既有审查读取）。
- Tool/MCP Gateway、Harness adapters 及 `agent_platform/production.py`：工具执行和运行时生产装配。

**已具备**

- Run 有持久化生命周期、checkpoint、取消、暂停、恢复和租约。
- LangGraph 多专家并行、Blackboard 共享上下文、人工审批和冲突处理已有代码基础。
- OCI sandbox 有镜像 digest、只读文件系统、网络/资源限制等安全约束；Tool Gateway 有监督执行与短期凭据设计。
- Harness benchmark 具备基准数据结构，但尚不能证明真实模型质量/成本的同任务比较。

**缺失/风险**

1. 多专家集成测试使用 InMemorySaver；PostgreSQL 重启恢复测试覆盖简化审批图，不足以证明真实多专家图并行分支、Blackboard 与 interrupt/resume 在重启后的完整恢复。
2. 生产默认 Runtime Builder/Gateway 入口并非所有工作流/工具路径都证明统一接线；必须用调用路径测试阻止旁路。
3. Worker 恢复时身份重检、上下文预算/裁剪、模型/工具结果可信度标注和跨 Agent 责任追踪需统一。
4. 沙箱的网络出口、文件系统挂载、凭据注入和运行时隔离需在真实部署拓扑做负向验收；本轮不申请/不连接真实环境。
5. LangGraph、其他 Harness、模型 Provider 的兼容性、取消/超时/流式/恢复语义和成本质量基准需执行同任务对比。

**建议**：不拆成独立微服务；先定义 RuntimeContext、ExecutionIdentity、ToolCallEnvelope、Checkpoint 和 Trace 的稳定契约，确保一个生产入口完成生命周期、策略和观测装配。

### A1-07 数据与知识引擎

**代码映射**

- `agent_platform/knowledge/service.py`：摄取、chunking、embedding、hybrid retrieval、rerank/query rewrite 接口。
- `agent_platform/knowledge/repository.py`：tenant/ACL-scoped repository port，包含集合、文档、索引、向量、connector cursor、document/chunk ACL grant/revoke、visible chunks。
- `agent_platform/memory/`：MemoryPrincipal 与租户/用户/项目/Agent 作用域（既有审查读取）。
- `agent_platform/graph/`：Graph governance、repository、query/schema（生产装配已读取）。
- `agent_platform/connectors/providers.py`、`control_plane/data_connectors.py`：Connector/provider 管理；此前盘点确认 provider 包括 PostgreSQL、MySQL、S3、飞书、Notion、通用 HTTP JSON 六类。

**已具备**

- 知识摄取与检索采用独立服务/Repository 抽象；检索 principal 包含租户和访问主体，Repository 提供 ACL-first 检索接口。
- 文档/Chunk ACL 支持 grant/revoke/replace；向量索引、Connector cursor 和知识集合有持久化接口。
- Memory 与 Graph 有独立服务域，避免把知识、记忆、关系查询混为单一表。

**缺失/风险**

1. Knowledge 前端 ACL 已有授权/撤销入口，但此前核验发现缺少 ACL 列表查询 API，刷新页面无法恢复完整授权状态。
2. 向量维度曾发现数据库 1024 与测试/默认配置 1536 不一致；需统一迁移、Embedding profile 和索引重建策略。
3. 文档删除/更新、对象存储文件、向量 chunk、Graph edge、Memory、缓存和 Connector 源端的级联一致性/删除传播未证明。
4. Tenant ACL 需覆盖异步 Worker、导入导出、对象存储签名 URL、全文/向量/Graph 检索和缓存 key；不能只凭 repository 接口命名判定隔离完成。
5. 连接器六类 provider 不等于 ERP/CRM/OA/HR/MES/PLM 等系统适配；PostgreSQL/MySQL Connector 对只读查询的保护此前发现依赖外部 DB 账号只读，建议增加应用层只读事务/语句约束。
6. 数据目录、分类分级、敏感数据识别、数据血缘、质量规则和保留策略尚未证明形成完整数据治理引擎。

**建议**：保留 Knowledge/Memory/Graph/Connector 服务；优先统一数据主体与 ACL 决策、对象存储最小权限、删除传播和 embedding 版本契约。

### A1-08 观测与计量引擎

**代码映射**

- `agent_platform/observability/instrumentation.py`：`safe_observation`、`safe_update`、`safe_metric`，从当前 Trace Span 传播 trace/span/parent 关联。
- `agent_platform/observability/metrics.py`：低基数 label 白名单；`InMemoryMetricsRecorder` 明确用于测试/单进程，生产应导出 Prometheus/OpenTelemetry 后端。
- `agent_platform/production.py`：装配 Run Trace repository、Evaluation、Prometheus metrics、OperationalHealthMonitor、Quota/alerts（既有生产组合读取）。
- `agent_platform/observability/`、`agent_platform/quotas/`：Trace、评测、用量和预算计量模块。

**已具备**

- Trace 上下文可传播；指标 label 有白名单，避免任意高基数字段。
- Run Trace、Evaluation、Quota、Alerts、Health Monitor 有服务/持久化/页面线索。
- 质量与成本页面/运行回放等前端入口在此前路由审查中已确认存在。

**缺失/风险**

1. InMemoryMetricsRecorder 不能作为多进程/多实例生产指标存储；需验证生产 exporter、指标保留和聚合。
2. Provider usage 的成本来源/模型定价版本、token 计量准确性、失败/重试成本和部门/团队/Agent 分摊尚未形成端到端账本证明。
3. SLA/SLO、错误预算、队列积压、任务成功率、P95/P99、告警升级和事件响应闭环尚未证明完整。
4. Evaluation 指标已有确定性 expected-term/document-id 等回归测试线索，但不足以代表开放式企业任务质量；Evaluation Gate 尚未证明接入所有生产资产发布路径。
5. 业务价值/ROI（节省工时、流程周期、自动化收益）及企业 AI 运营报表未形成可核验闭环。
6. Trace、Approval、Tool side effect、Artifact、Task/Run 的统一关联 ID 和长期审计查询需形成完整链路证据。

**建议**：保留 Trace/Evaluation/Quota/Alert；统一 UsageLedger 事件口径，绑定 tenant/org/team/agent/run/provider/model/version，并把 Eval evidence 与资产发布审批关联。

## 3. 横向依赖与边界判断

八大引擎应是模块化平台中的统一职责边界，不要求机械拆成八个微服务。推荐依赖方向：

1. 身份与组织为所有引擎提供可信 Actor/Tenant/ResourceScope。
2. AI 资产提供精确版本快照，供任务、Runtime、Evaluation 和审计引用。
3. 任务/调度创建 Run 并持久化状态；事件引擎承担可靠异步触发/分发。
4. 策略审批为 Runtime/Connector/Knowledge 数据访问与业务副作用提供服务端授权。
5. Runtime 通过 Tool Gateway 调用外部能力，使用 Sandbox 隔离并写入 Trace/Audit。
6. 数据知识按统一主体执行 ACL 检索，向 Runtime 提供有来源的上下文。
7. 观测计量关联上述全部动作，产出质量、成本、可靠性和业务价值证据。

关键断点：Worker 身份与策略复核、资产发布与 Evaluation Gate、事件 at-least-once 与副作用幂等、知识 ACL 与异步/对象存储、Trace 与成本/业务结果关联。

## 4. 去重后的研发缺口 Backlog

| ID | 优先级 | 缺口 | 依赖 | 可验证验收标准 |
|---|---|---|---|---|
| A1-B01 | P0 | 统一生产 Runtime → Tool Gateway → Policy/Approval → Audit 主链路 | Identity、Registry、Approval | 对每个生产 Run 路径做调用图/集成测试；任何旁路工具执行均被拒绝；拒绝/成功均产生关联审计 |
| A1-B02 | P0 | Worker 执行时主体/权限复核与撤权传播 | Identity、Task/Run | 用户/Agent/凭据撤销后，queued、retry、resume Run 均拒绝；跨租户/部门负向测试通过 |
| A1-B03 | P0 | DB、Object Storage、Connector 最小权限 | Identity、Knowledge、Connector | API/Worker 不持有数据库 superuser/MinIO root；只读 Connector 写操作被拒绝；Release Gate 检查角色/ACL |
| A1-B04 | P0 | 审批 verifier 与 ToolEffect reconciliation 生产接线 | Policy、Approval、Runtime | 真实 resolver 授权、审批消费、审计 sink 集成测试通过；缺失依赖 fail closed 且不产生 500 |
| A1-B05 | P1 | Task/Run/Approval/Event/Artifact 统一资源关系与副作用账本 | Task、Event、Identity | 数据模型和 API 支持完整关联；崩溃窗口重放不重复业务副作用；支持补偿/人工接管 |
| A1-B06 | P1 | Consumer 幂等与事件契约治理 | Event、Task | event_id 幂等由通用执行框架强制；重复投递、DLQ、replay、多实例恢复测试通过 |
| A1-B07 | P1 | 全资产生命周期与 Evaluation 发布 Gate | Registry、Evaluation、Approval | Agent/Workflow/Skill/Tool/Prompt/Connector 发布绑定版本、依赖、审批、Eval evidence；回滚可追溯 |
| A1-B08 | P1 | 知识 ACL/删除传播/Embedding 版本一致性 | Identity、Knowledge、Object Storage | API/Worker/向量/文件/Graph/Memory ACL 矩阵通过；删除后所有副本按 SLA 清除；维度迁移可回滚 |
| A1-B09 | P1 | 独立 Run Worker 与可靠调度弹性 | Task、Event、Runtime | API/Worker 可独立扩缩；租约过期接管、优先级、公平性、取消/恢复竞态测试通过 |
| A1-B10 | P2 | 统一成本、质量、SLA 与价值计量 | Runtime、Event、Identity | Token/成本账本按组织层级归集；真实任务评测与 SLO/ROI 报表可复现 |
| A1-B11 | P2 | DigitalEmployee 组织身份与职责模型 | Identity、Asset、Task | 数字员工具有组织归属、岗位职责、负责人、权限包、任务成果与绩效审计 |
| A1-B12 | P2 | 生态资产信任与伙伴分发闭环 | Asset、Identity、Policy | 资产签名/扫描/认证/隔离安装/升级/撤销/授权可审计 |

## 5. 本轮验证记录

| 验证项 | 本轮结果 | 说明 |
|---|---|---|
| main 基线 | 已确认 | `b622d5fc9eab1c79c28dcaa13367eec3c138679d`，GitHub commit search 返回 main 最新提交 |
| 八大引擎源码映射 | 已完成静态读取 | 直接读取 identity、registry、runtime lifecycle、PostgreSQL lifecycle、outbox、approval policy/service/verifier、knowledge service/repository、observability instrumentation/metrics、production composition |
| 测试文件/CI 证据核对 | 部分完成 | 对照现有报告及 Issue #848 评论中的测试/CI 记录；本轮没有执行完整测试 |
| 本地自测 | 未运行 | 当前环境无仓库 checkout，DNS/网络限制导致不能 clone；不连接真实依赖 |
| CI | 由 PR 自动触发，待结果 | 不以静态审查替代 CI；PR 创建后核对该 commit 的检查状态 |
| 真机/外部资源 | 按要求跳过 | 未申请、未连接真实服务器、数据库、Redis、对象存储或模型服务 |

### 可复现验证建议（由具备完整 checkout 的 CI 执行）

- `pytest -q tests/test_platform_postgres_integration.py`
- `pytest -q tests/test_redis_streams_integration.py`
- `pytest -q tests/test_issue_755_c_dual_mode_integration.py`
- `pytest -q tests/test_*identity* tests/test_*approval* tests/test_*registry* tests/test_*runtime*`
- `ruff check agent_platform/identity agent_platform/control_plane agent_platform/runtime agent_platform/events agent_platform/approval agent_platform/knowledge agent_platform/observability`
- `mypy agent_platform/identity agent_platform/runtime agent_platform/events agent_platform/approval`

以上为建议命令，不代表本轮已运行或通过。

## 6. 审查边界

- 本交付只覆盖 A1 八大引擎；不声称完成 Issue #848 全部审查验收。
- 未修改业务代码、数据库迁移、运行时行为或前端页面。
- 未合并任何 PR；PR 仅作为文档交付供审阅。
- 代码存在、测试存在、CI 通过、端到端可运行、生产可用是不同结论。本报告对未执行的验证明确标为未运行/待验证。
