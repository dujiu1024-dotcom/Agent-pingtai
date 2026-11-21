# B2 专项审查：十四个产品中心（1–7）

- Issue：[#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- 本轮范围：仅产品中心 1–7：智能体管理、多智能体协作、行业智能应用、工作流自动化、任务执行与会话、模型与工具、企业知识与记忆
- 代码基线：`main` @ `b622d5fc9eab1c79c28dcaa13367eec3c138679d`
- 审查日期：2026-11-21
- 审查方法：检查后端领域模块、API router、组合根、PostgreSQL repository/migration、前端 React 页面和路由、专项测试文件、CI workflow 及产品目录；以代码和可追溯测试为证据，不以 README/历史 Issue 名称判定完成。
- 验证限制：本轮没有启动项目依赖栈或数据库，没有真实模型/外部业务系统凭据；未做真机和生产环境验证。专项测试文件存在不代表本轮执行通过；本轮 CI 以 PR Checks 实际结果为准。

## 一、结论摘要

| 中心 | 成熟度 | 已有可复用实现 | 核心缺口 |
|---|---|---|---|
| 1. 智能体管理 | 基础已完成 | Agent 版本注册、发布/禁用/回滚、模型/工具/技能/工作流引用、管理页面、Run 固定快照 | Agent 调试/测试台、发布前依赖验证与沙箱试运行、完整运行表现/资产依赖视图和操作闭环仍不足 |
| 2. 多智能体协作 | 部分实现 | LangGraph 多专家编排、并行分支、专家注册、Blackboard 持久化、冲突复核、审批、人类协同 UI | 协作策略/拓扑产品化、委派范围与责任模型、跨 Agent 资源预算/隔离、失败恢复和结果质量评估闭环不足 |
| 3. 行业智能应用 | 尚未建设（产品中心）/基础素材存在 | 通用 Agent/Workflow/Skill/Tool 注册与模板目录可作为底座；仓库存在项目/客户诊断类文档模板 | 未见行业方案目录、行业 Agent/Workflow 交付包、参数化配置/安装/验收/行业数据模型和独立 UI/API |
| 4. 工作流自动化 | 部分实现 | Workflow 作为版本化 registry 资产；JSON nodes/edges 编辑页；LangGraph 执行基础、任务调度和 Run 轨迹 | 缺少可视化画布、节点类型设计器、条件/循环/人工任务/定时触发的完整产品闭环、workflow 专用运行/调试/版本依赖图 |
| 5. 任务执行与会话 | 基础已完成 | Task Center、Run lifecycle/worker、Postgres 持久化、取消/重试/人工干预、会话消息/文件资产/上下文快照、任务 UI | 长任务多阶段恢复和业务副作用 exactly-once 仍需外部执行语义；会话 UI/任务产物体验和任务-会话-审批-产物的统一导航有缺口 |
| 6. 模型与工具 | 基础已完成（分散模块） | Provider/Template/路由策略 registry、模型调用路由与预算、工具注册/MCP Gateway、风险级别/Schema、工具审计和执行幂等 | 缺少统一模型/工具运营中心：连通性测试、能力/兼容矩阵、供应商健康/成本/额度运营、MCP server 生命周期与权限审查体验 |
| 7. 企业知识与记忆 | 基础已完成 | 知识集合/文档/分块/Embedding/RAG/引用、ACL 检索、连接器增量同步、版本化记忆/冲突/审核/删除、知识图谱 | 数据源适配覆盖有限；知识治理/索引重建/质量运营不足；记忆策略、保留与用户控制、图谱和 RAG/Agent 使用的完整关联仍需补齐 |

**总判断：** B2 七个中心中没有一项应直接标记为完整“已完成”。中心 1、5、6、7 已有真实后端机制和可用的前端功能切片；中心 2、4 有较强执行内核但产品化程度有限；中心 3 主要是可复用资产底座和通用业务模板，尚未形成行业应用中心。现有代码应优先整合与补齐，不建议重写 Runtime、Registry、Task/Run、Knowledge 和 Memory 的核心实现。

## 二、逐项代码审查

### 1. 智能体管理中心

**成熟度：基础已完成。**

#### 代码与页面证据

- 资产领域：`agent_platform/control_plane/registry.py`
  - `_KINDS` 包含 agent、workflow、tool、skill、model_provider、model_template、routing_policy。
  - `RegistryVersion` 表达 tenant、kind、item_id、version、definition、status、created_by、published_at。
  - `RegistryService` 提供草稿创建、发布、回滚、禁用和 active version 解析；definition 禁止写入明文凭据。
- 持久化：`agent_platform/control_plane/postgres.py` 的 `PostgresRegistryRepository`；迁移 `agent_platform/storage/postgres/011_registry_sessions_assets.sql`、`024_registry_runtime_governance.sql`。
- API：`agent_platform/api/registry.py`
  - `GET /api/platform/v1/registry/{kind}`
  - 创建版本、发布版本、回滚、版本 diff、版本详情、版本列表、禁用和 active version 查询。
  - 从认证 Principal 获取 tenant/actor，并执行服务端 authorize。
- 前端：`frontend/src/features/agents/AgentDefinitionPage.tsx`；`frontend/src/App.tsx` 路由级加载；`frontend/src/routes.ts` 的 `/agents`、`/agents/:id`。
  - 页面读取 registry agent 列表、active version、versions；编辑名称/说明/instructions/model template/tool/skill/workflow 引用；保存新版本并可发布。
- Runtime 关联：`agent_platform/runtime/workbench_tasks.py` 在 Task 执行时解析 Agent/Model Template，并将 Agent version、template version 和 registry snapshot 记录到 Run；`agent_platform/runtime/delegation_task_center.py` 对子 Run 校验 pinned runtime。

#### 目标功能对照与缺失

| 目标功能 | 当前状态 | 具体缺失/边界 |
|---|---|---|
| 创建、配置 Agent | 基础已实现 | 页面配置主要是元数据/instructions 和资源引用；没有统一可视化配置所有 Runtime/上下文/安全/输出约束 |
| 版本管理、发布、停用、回滚 | 基础已实现 | Registry 有版本状态机和 API；发布前的依赖兼容、工具授权、模型可用性、回归评测和环境晋级需进一步统一 |
| 调试与测试 | 部分实现 | 运行任务可由 Task Center 执行；未见 Agent 专属调试台、变量/上下文检查、工具模拟、单步执行和测试集批量回归入口 |
| Agent 依赖视图 | 部分实现 | definition 存 model_template/tool/skill/workflow refs；Tool/Skill 可显示 used_by；缺少完整依赖图、循环依赖检查、版本锁定和影响分析 UI |
| 运行监控与质量 | 部分实现 | Task/Run/Trace/Cost 已存在；Agent 级成功率、质量、成本、版本对比和运营视图未形成统一页面 |
| 生命周期与治理 | 部分实现 | 具备 tenant/actor/version/status；Agent owner、业务岗位、负责人、发布审批、下线影响面等管理语义不完整 |

#### 测试证据

- `tests/test_model_registry_resolver.py`
- `tests/test_session_asset_run_traceability.py`
- `tests/test_workbench_task_runtime.py`
- `tests/test_tool_registry_catalog.py`
- `frontend/src/routes/agent-definition-route.test.ts`

**建议：** 保留 Registry 与 Run snapshot；P1 增加 Agent Test Bench 和发布预检；P2 增加依赖图、版本比较和 Agent 运营质量页。

### 2. 多智能体协作中心

**成熟度：部分实现。**

#### 代码与页面证据

- 编排图：`agent_platform/workflows/multi_expert.py`
  - `MultiExpertState` 表达 tenant/task/run/objective、专家分配、贡献、综合结果、冲突、审批、最终结果和状态。
  - `expert_dispatch_payloads` 为并发专家分支生成隔离的深拷贝上下文；贡献结果按 expert id / blackboard id 确定性排序，避免把并发完成顺序误当成优先级。
  - Review → resolve conflicts → human approval → archive/reject 有明确路由。
- 专家注册：`agent_platform/workflows/expert_registry.py`
  - `ExpertProfile` 声明能力；`ControlPlaneExpertRegistry` 只解析租户 Registry 中 published active Agent 版本。
  - `ExpertAssignment` 对任务、专家和所需能力进行契约校验。
- Blackboard：`agent_platform/workflows/blackboard_services.py` 将 proposal、冲突、决策、审批和最终产物写入 Blackboard，使用确定性 ID 做幂等保护；`agent_platform/blackboard/postgres_repository.py` 为 PostgreSQL 持久化并通过 outbox 发布事件。
- API：`agent_platform/api/blackboard.py` 提供 tenant/project/task/run scope 授权、版本冲突控制、证据引用校验和 SSE 事件流；`agent_platform/api/graph.py` 为图谱数据提供权限范围。
- 前端：`frontend/src/features/collaboration/AgentCollaborationCenter.tsx`；`frontend/src/App.tsx`。
  - 页面展示 Blackboard 条目、事件状态、贡献/决策/证据及协作事件；对 403/404/409/422/503 给出用户提示。
- 测试：
  - `tests/test_multi_expert_workflow_integration.py`
  - `tests/test_blackboard_multi_expert_services.py`
  - `tests/test_blackboard.py`
  - `tests/test_blackboard_event_delivery.py`
  - `tests/test_blackboard_event_replay.py`
  - `tests/test_blackboard_sse.py`

#### 具体缺失与风险

1. 当前多专家工作流的核心服务通过 `MultiExpertServices` / callback 注入，编排图定义了阶段和协议；专家规划、模型执行、综合、审查、冲突解决的具体质量依赖注入的业务实现，不能仅凭 LangGraph 节点存在就认定平台已有自动规划能力。
2. 协作页面偏 Blackboard/事件态势，并非完整的团队设计器：未见可配置团队拓扑、角色职责、专家能力匹配、并行/串行策略、预算和最大轮数的可视化编辑闭环。
3. 委派执行可由 `TaskCenterChildRunExecutor` 创建 durable child Run，但委派授权目前需与 Agent pinned tool set 对齐；尚缺可单独表达委派目的、数据范围、动作范围、预算上限、有效期、委派审批和责任链的完整产品模型。
4. 并发执行、部分专家失败后的补偿/重新分配、断点恢复与人工暂停/恢复的完整 E2E 证据不足；单元测试的 InMemory saver/repository 不能替代生产 LangGraph + PG checkpointer + worker 集成。
5. 冲突审查节点有逻辑和 Blackboard 记录，但缺跨任务的协作质量基准、事实一致性/证据充分性指标和专家表现对比。

**建议：** P1 把团队配置、委派授权和预算/失败策略作为 Registry/Policy/Task 的组合视图；补生产持久化 checkpointer 的多专家 E2E。保留 Blackboard 作为共享事实层，避免另建协作消息数据库。

### 3. 行业智能应用中心

**成熟度：尚未建设（产品中心）；通用资产基础可复用。**

#### 代码与页面证据

- 可复用资产底座：`agent_platform/control_plane/registry.py` 支持 Agent/Workflow/Tool/Skill 等定义版本；`agent_platform/extensions/manifest.py`、`package.py`、`dependency_graph.py`、`publishers.py` 支持扩展清单、依赖和发布者元数据。
- 前端通用资产页：`frontend/src/features/agents/AgentDefinitionPage.tsx`、`frontend/src/features/workflows/WorkflowDesignerPage.tsx`、`frontend/src/components/crud/registry-configs.ts`。
- 文档模板：仓库 `templates/` 中有客户诊断、需求说明、方案对比、报价和验收模板；它们是项目交付模板，不是行业 AI 应用资产包。
- 代码树中未发现独立 `industry` / `vertical solutions` 领域模块、行业方案目录页面或行业应用 API 路由；当前前端路由中也没有行业应用中心入口。

#### 目标功能对照与缺失

| 目标功能 | 当前状态 | 具体缺失 |
|---|---|---|
| 行业模板/场景目录 | 尚未建设 | 无可搜索行业目录、行业分类、场景标签、适用规模/系统/数据要求 |
| 行业 Agent/Workflow 方案包 | 仅有通用资产底座 | 无经验证的行业 Agent、Workflow、Prompt、Knowledge schema、Tool/Connector 组合交付包 |
| 配置化交付 | 尚未建设 | 无参数向导、客户差异配置、环境变量/连接器映射、部署预检和安装器 |
| 行业数据模型/知识模板 | 尚未建设 | 无行业术语、实体关系、指标口径、数据映射和知识模板版本治理 |
| 验收与运营 | 尚未建设 | 无行业任务集、效果基线、交付验收模板与运行效果报表 |
| 伙伴共建 | 部分底座 | Extension publisher/manifest 是扩展发布底座，不等于行业解决方案认证、签名、兼容性验证、授权和收益分配 |

**建议：** 不单独另建一套 Agent Runtime。P2 基于现有 Asset Registry + Extension package 形成 Solution Package 资源类型（元数据、依赖、配置 schema、安装计划、验收数据集、版本/授权），先选定一个具体行业场景形成端到端参考包。

### 4. 工作流自动化中心

**成熟度：部分实现。**

#### 代码与页面证据

- Registry：`agent_platform/control_plane/registry.py` 将 `workflow` 作为版本化资产 kind；`agent_platform/api/registry.py` 提供版本创建、发布、回滚、diff 和 active 查询。
- 前端：`frontend/src/features/workflows/WorkflowDesignerPage.tsx`
  - 读取 workflow registry、active version、versions。
  - 编辑 name/description 和 JSON `nodes` / `edges`，保存新版本并发布。
  - 当前是 JSON 文本编辑，不是可拖拽节点画布。
- Runtime：`agent_platform/workflows/multi_expert.py` 使用 LangGraph 构建专家协作图；`agent_platform/runtime/langgraph_adapter.py` 提供 LangGraph adapter；`agent_platform/runtime/workbench_tasks.py` 将 Agent/Workflow/Task 的执行关联到 Run lifecycle。
- API：Workflow 定义通过 registry API；任务执行通过 `agent_platform/api/workbench.py`，Run 过程通过 runs API；前端协作中心展示部分运行事件。
- 测试：`tests/test_langgraph_gateway_contract.py`、`tests/test_langgraph_run_executor.py`、`tests/test_workbench_task_runtime.py`、`tests/test_multi_expert_workflow_integration.py`。

#### 目标功能对照与缺失

| 目标功能 | 当前状态 | 具体缺失 |
|---|---|---|
| 声明式 Workflow 定义/版本 | 基础已实现 | JSON nodes/edges 结构尚缺完整 schema/版本兼容和迁移工具 |
| 可视化编排画布 | 尚未建设 | 当前页面用 JSON 文本编辑，无节点拖拽、连线、缩放、校验和图形调试 |
| 节点类型 | 部分实现 | Runtime 有 LangGraph 节点/工具执行基础；未见产品级节点目录和配置 UI（模型、工具、条件、循环、并行、人工任务、子流程、数据转换等） |
| 条件分支/循环/并行 | 部分实现 | LangGraph 可表达流程逻辑，但尚无通用 Workflow DSL/执行语义和 UI 覆盖所有节点类型 |
| 人工审批节点 | 部分实现 | Approval API/服务可用，但没有证据证明它已成为任意 Workflow 可编排节点并支持等待/恢复 |
| 定时触发 | 部分实现 | Scheduler API/Job 模块存在；尚未证明任意 workflow 可绑定 schedule、时区、重叠策略和运行历史 |
| 调试/单步/变量 | 尚未建设 | 缺节点断点、输入输出查看、重放和版本对比调试体验 |
| 运行/失败恢复 | 部分实现 | Run lifecycle、checkpoint、worker 已有；Workflow 版本和 Run snapshot 的完整 pinned graph 及恢复 E2E 仍需验证 |

**建议：** P1 先明确 Workflow DSL/schema 和 node contract，使用现有 LangGraph adapter 执行；之后再实现画布，避免先做 UI 再出现第二套流程运行时。

### 5. 任务执行与会话中心

**成熟度：基础已完成。**

#### 代码与页面证据

- Task API：`agent_platform/api/workbench.py` 定义任务列表、提交、查询、关联 Runs、取消、重试、死信列表/重试/解决等服务协议和路由；请求身份由 Principal 注入。
- Task/Run runtime：`agent_platform/runtime/workbench_tasks.py`
  - `RegistryBackedTaskCenterService` 将 Task 与 registry active version、Run、model snapshot、session/project 关联。
  - `RunExecutionWorker` 复用统一 Run lifecycle、lease、quota、checkpoint 和 LangGraph executor。
  - 支持超时策略、优先级、重试幂等和任务状态查询。
- Run / intervention：`agent_platform/runtime/lifecycle.py`、`agent_platform/api/runs.py`、`agent_platform/api/approvals.py`；任务 UI 支持取消、重试、人工决策/审批/上下文补充/接管及工具副作用核对状态。
- Session/Asset：`agent_platform/control_plane/sessions_assets.py`、`agent_platform/control_plane/postgres.py` 的 Postgres Session/Asset repository；`agent_platform/api/sessions.py` 提供会话、消息、文件、导出/归档等 API；`agent_platform/runtime/session_persistence.py` 将 durable Run 与 session history 关联。
- 数据迁移：`011_registry_sessions_assets.sql`、`025_workbench_task_execution.sql`、`028_task_center_interventions.sql`、`031_workbench_task_execution.sql`、`082_session_conversation_context.sql` 等。
- 前端：`frontend/src/features/tasks/TaskCommandCenter.tsx`、`frontend/src/routes/route-config.ts` 的 workspace/task/run routes；`frontend/src/App.tsx` 加载任务页、详情和 Run trace 子页面。
- 测试：
  - `tests/test_workbench_task_center.py`
  - `tests/test_workbench_task_runtime.py`
  - `tests/test_workbench_task_contract.py`
  - `tests/test_task_center_interventions_postgres_integration.py`
  - `tests/test_session_management_api.py`
  - `tests/test_session_data_model.py`
  - `tests/test_session_run_persistence.py`
  - `tests/test_session_asset_run_traceability.py`
  - `tests/test_session_message_pagination.py`

#### 具体缺失与风险

1. Task 与 Run 目前是平台运行核心；但“长任务恢复后不重复执行业务副作用”不能只靠 Run checkpoint。需要依赖 Tool Effect idempotency/reconciliation 和外部系统幂等键/补偿协议；非幂等第三方接口无法由平台单方面保证 exactly-once。
2. Task Center 页面已有状态和干预功能，但 Session 对话体验、任务产物预览/版本/交付、Task↔Session↔Run↔Approval↔Artifact 的统一详情导航仍不完整。
3. 多任务并行调度、优先级公平性、跨租户资源隔离、队列容量和 worker 自动扩缩容属于运行层能力；存在模块与测试不代表企业生产容量已验收。
4. Session/Asset API 与 Run context persistence 分属不同领域模块；删除/归档策略、历史会话保留、文件资产版本和 Run 追踪的统一生命周期需持续治理。
5. Session UI 当前主要体现在任务/会话资源路由和相关组件；未见独立完整的会话工作台来统一流式对话、历史检索、附件管理、上下文引用和人工接管。

**建议：** 保留 Task/Run/Session 分层；P1 做任务产物和会话工作台的产品闭环，并增加外部副作用崩溃窗口集成测试；P2 完善容量和部门/团队调度策略。

### 6. 模型与工具中心

**成熟度：基础已完成（后端模块存在，前端分散）。**

#### 代码与页面证据

- 模型资产：`agent_platform/models/templates.py` 定义 ModelTemplate 与凭据字段隔离；`agent_platform/models/registry_resolver.py` 从 Run pinned registry snapshot 解析 Provider/Template；`agent_platform/models/runtime_router.py` 提供调用、fallback、circuit breaker、usage/cost 和 trace。
- 模型 Registry kinds：`agent_platform/control_plane/registry.py` 的 model_provider/model_template/routing_policy。
- 工具目录：`agent_platform/tools/registry_catalog.py`、`agent_platform/tools/registry_tools.py`、`agent_platform/tools/connector_catalog.py`。
- 安全 Gateway：`agent_platform/tools/gateway.py`、`gateway_mcp.py`、`gateway_mcp_http.py`、`mcp_adapter.py`、`execution_contract.py`；运行时通过 Gateway 和允许工具集合执行，不从用户 registry definition 直接加载任意 Python handler。
- 前端：`frontend/src/components/crud/registry-configs.ts`
  - `/admin/models/providers`、`/admin/models/templates`、`/admin/models/routing`。
  - `/admin/tools`、`/admin/skills`；模型 provider 模板字段包含 provider_type/base_url/api_key_ref，工具字段包含 risk_level/input_schema。
  - 通用 RegistryCatalogPage/CrudListPage 复用 CRUD。
- 测试：`tests/test_model_templates.py`、`tests/test_model_registry_resolver.py`、`tests/test_model_runtime_router.py`、`tests/test_tool_registry_catalog.py`、`tests/test_tool_gateway.py`、`tests/test_tool_gateway_execution_contract.py`、`tests/test_langgraph_gateway_contract.py`、`tests/test_strands_agents_adapter.py`。

#### 具体缺失与风险

1. 模型注册/模板/路由已有管理能力，但缺少单一运营视图：模型连通性测试、能力探测、供应商健康、错误率/延迟/价格、凭据有效性和故障切换演练。
2. Provider 当前以 OpenAI-compatible 为主；原生厂商能力、结构化输出、工具调用、视觉/音频能力、缓存与错误语义需要按 adapter 能力矩阵明确支持边界。
3. 工具定义与 MCP Server 配置目前主要是元数据/连接信息管理；MCP server 安装/版本/健康/证书/权限 scopes/资源暴露/审批/撤销/审计生命周期尚未形成完整控制台。
4. 工具测试目前有契约和 Gateway 测试；缺少工具配置后的安全连通性测试台、参数样例运行、权限模拟、沙箱试运行和回归验证页面。
5. 工具 risk_level/input_schema 有基础；但工具输出 schema、数据分类、网络域名范围、资源额度、人工审批阈值与业务副作用核验需形成统一配置和运行策略。
6. 模型预算、价格表和路由策略分布于 quota/model/observability 模块；缺少以部门/Agent/任务为视角的模型与工具中心成本联动。

**建议：** 复用 Registry、Model Runtime Router、Tool Gateway 和 MCP adapter；P1 补模型/工具 test console 与 provider capability matrix；P2 做模型和工具的统一生命周期/运营看板。

### 7. 企业知识与记忆中心

**成熟度：基础已完成。**

#### 代码与页面证据

- 知识领域：`agent_platform/knowledge/service.py`、`ingestion.py`、`chunking.py`、`retrieval.py`、`rag.py`、`citations.py`、`catalog.py`、`postgres_repository.py`。
  - 支持 PDF/Markdown/HTML 等解析入口、清洗/切块/哈希/版本、Embedding、hybrid retrieval、rerank/query rewrite 插口、RAG 答案引用校验和无证据 abstain。
- Provider：`agent_platform/knowledge/providers.py` 提供真实 OpenAI-compatible embedding/reranker/OCR HTTP adapter，强制 HTTPS 和错误 fail-closed；`knowledge/ollama.py` 为本地模型适配。
- Connector：`knowledge/connectors.py` 定义增量同步/cursor/tombstone/revoke contract；仓库存在 `google_drive_connector.py` 和 `sharepoint_connector.py` 实际适配器；`agent_platform/control_plane/connector_sync.py`、`connector_workers.py` 提供同步 worker。
- 知识 ACL：`agent_platform/knowledge/acl_mapping.py`、`knowledge/retrieval.py` 和 `storage/postgres/032_knowledge_acl_enforcement.sql`、`067_knowledge_catalog.sql`、`068_knowledge_embedding_versions.sql`、`069_knowledge_chunk_acl_invalidation.sql`、`079_knowledge_collections.sql`、`080_knowledge_collection_lifecycle.sql`；API 从 KnowledgePrincipal 取得 tenant/project/user，不信任 request body 中 tenant。
- 记忆领域：`agent_platform/memory/service.py`、`extraction.py`、`retrieval.py`、`review.py`、`conflicts.py`、`deletion.py`、`maintenance.py`、`postgres_repository.py`。
  - 支持 user/agent/project/tenant scopes、candidate/active/conflict/superseded/archived/expired/deleted 状态、确认后检索、冲突处理、版本化、过期/删除和来源边界。
- 图谱：`agent_platform/graph/schema.py`、`pipeline.py`、`query.py`、`governance.py`、`postgres_repository.py`；API `graph.py` / `graph_extraction.py`；图谱页面可查实体/关系、质量指标和抽取任务。
- 前端：`frontend/src/features/knowledge/KnowledgeWorkbench.tsx`、`KnowledgeGraphPage.tsx`、`frontend/src/features/memories/MemoryGovernancePage.tsx`；路由由 `frontend/src/App.tsx` 注册。
  - 知识工作台支持集合/文档/分块/索引/检索问答/引用/ACL grants 等视图。
  - 记忆治理页支持候选审核、冲突、检索和导出。
- 测试：
  - `tests/test_knowledge_content_pipeline.py`
  - `tests/test_knowledge_service.py`
  - `tests/test_knowledge_provider_adapters.py`
  - `tests/test_knowledge_acl_mapping.py`
  - `tests/test_knowledge_acl_propagation.py`
  - `tests/test_knowledge_retrieval_policy.py`
  - `tests/test_knowledge_catalog_api.py`
  - `tests/test_knowledge_catalog_postgres_integration.py`
  - `tests/test_knowledge_memory_api.py`
  - `tests/test_memory.py`
  - `tests/test_memory_retrieval.py`
  - `tests/test_memory_deletion.py`
  - `tests/test_memory_conflicts.py`
  - `tests/test_graph_governance.py`
  - `tests/test_graph_postgres_repository.py`

#### 具体缺失与风险

1. 当前明确的外部知识连接器证据主要是 Google Drive、SharePoint；其他 ERP/CRM/OA/飞书/钉钉/企业微信/数据库等不能因 connector protocol 存在而认定已适配。
2. 上传入口列出的支持类型主要为 PDF/Markdown/HTML；Office parser 模块存在，但当前上传 API 允许的类型与可解析格式需要统一，不能将 parser 存在等同于前端已开放。
3. Embedding schema 默认 vector(1536)，更换维度需迁移；多模型 embedding 索引并存、切换、回填、回滚和成本治理仍需完善。
4. Knowledge ACL 已覆盖租户/项目/私有 scope 与 chunk 级 ACL invalidation；仍需持续验证源系统权限撤销传播、同步失败时旧权限是否及时撤销，以及向量/缓存/图谱/导出路径的一致过滤。
5. 记忆提取与候选审核机制存在；需要明确企业级记忆保留策略、用户可见/可纠正/可导出/可删除控制、敏感内容规则、自动归纳置信度和不同 scope 的管理员责任边界。
6. 图谱提取、候选治理、删除传播已有代码；图谱仍需在知识更新、权限变更、删除、记忆和 RAG 之间建立更完整的血缘与增量一致性。
7. 知识质量评测与数据集有模块，但没有证明生产知识更新的 freshness、召回/准确率、引用正确率、权限漏检率等指标形成持续运营门禁。

**建议：** 保留 Knowledge/Memory/Graph 的领域边界和共享 tenant principal；P1 建立连接器支持矩阵、权限撤销一致性测试和索引版本运营；P2 统一知识质量与记忆用户控制台。

## 三、七个中心的端到端产品链路映射

| 产品中心 | 前端入口 | API/后端 | 数据/持久化 | 关键测试 | 链路判断 |
|---|---|---|---|---|---|
| 智能体管理 | AgentDefinitionPage、/agents | registry API、RegistryService | PostgresRegistryRepository、registry migrations | registry resolver、agent route、workbench runtime | 有前后端链路；Agent 调试/发布预检不完整 |
| 多智能体协作 | AgentCollaborationCenter | Blackboard API、MultiExpertServices | PostgresBlackboardRepository、event/outbox | multi-expert integration、blackboard tests | 有协作执行内核；生产 E2E 和团队配置产品不足 |
| 行业智能应用 | 未发现专属入口 | 未发现专属行业 API | 无行业 Solution Package 表/模型证据 | 未发现行业包验收测试 | 产品中心缺失 |
| 工作流自动化 | WorkflowDesignerPage | registry API + Runtime/Task API | workflow registry version + Run snapshots | LangGraph executor/workbench tests | 定义/执行片段存在；可视化 DSL 和完整触发/调试闭环不足 |
| 任务执行与会话 | TaskCommandCenter、Task/Run 路由 | workbench/sessions/runs APIs | Postgres RunState/Session/Asset | workbench/session integration tests | 核心链路基础已完成；会话体验和业务副作用恢复仍有缺口 |
| 模型与工具 | RegistryCatalogPage 配置路由 | registry、model router、tool gateway、MCP | registry、secret refs、usage ledger | model/tool gateway tests | 管理与执行分布于多个模块，统一运营体验不足 |
| 企业知识与记忆 | KnowledgeWorkbench、KnowledgeGraphPage、MemoryGovernancePage | knowledge/catalog/upload/memory/graph APIs | Postgres docs/chunks/ACL/collections/memory/graph | knowledge/memory/graph tests | 知识、记忆、图谱有真实领域实现；数据源覆盖与质量运营不足 |

## 四、横向架构判断

### 可复用的核心底座

1. **Asset/Registry**：Agent、Workflow、Tool、Skill、Model Provider/Template/Route 已使用同一 tenant-scoped versioned registry；避免再建多套资产表。
2. **Runtime/Task/Run**：Workbench Task Center 复用 Run lifecycle、worker、quota、snapshot 和 checkpoint；新产品应在这条链路上扩展。
3. **Blackboard**：多 Agent 协作事实、证据、冲突和审批记录已有 durable repository 与 outbox；继续作为协作共享状态层。
4. **Knowledge/Memory/Graph**：领域边界清晰，可通过统一 principal、trace/run/session 引用协作，不建议合并成一个巨大服务。
5. **Tool Gateway**：模型/Agent/Workflow 工具调用应统一经过 Gateway，而不是让前端或自定义 Agent 直接访问外部服务。

### 主要架构风险

- Registry 的资源定义统一，但 Agent、Workflow、Skill、Tool、Model 的配置 schema/依赖兼容性尚未统一治理。
- Workflow UI 是 JSON 编辑器，Runtime 是 LangGraph；尚未建立稳定、可校验、可迁移的通用 Workflow DSL。
- 多 Agent 的执行逻辑与协作 UI 之间有依赖注入边界；产品化配置、权限、预算、执行策略不能继续散落在 callbacks。
- 模型与工具中心的管理 API、运行路由、凭据引用、健康监控和成本视图分散，使用者难以完成“配置—测试—授权—发布—观察”一站式流程。
- 知识与记忆涉及同一用户上下文但使用不同 principal/ACL/scope 模型；应统一授权语义和审计关联，避免为了统一 UI 而混淆权限模型。
- 行业方案没有独立资产类型，若直接用多个 Agent/Workflow 手工拼装，会缺少交付包依赖、客户参数、升级兼容和验收机制。

## 五、P0/P1/P2 差距 Backlog（仅 B2）

| 优先级 | 工作项 | 目标中心 | 依赖 | 可验证验收标准 |
|---|---|---|---|---|
| P0 | 发布预检：Agent/Workflow 依赖、工具权限、模型模板、策略和评测统一 gate | 1、4、6 | Registry、Policy、Evaluation、Runtime | 缺失/禁用依赖、越权工具、模型不可用、评测退化均阻止发布；有 API/UI/CI 测试 |
| P1 | Agent Test Bench | 1 | Registry、Run/Trace、Tool Gateway | 可指定版本、输入、上下文、工具权限；支持模拟/真实授权运行、单步轨迹、批量数据集回归 |
| P1 | 多 Agent 团队配置与委派策略模型 | 2 | Registry、Policy/Approval、Task/Quota、Blackboard | 可声明角色/能力/范围/预算/审批/责任人；跨租户拒绝、超预算停止、失败重派和恢复有 PG E2E |
| P1 | Workflow DSL 与节点契约 | 4 | Registry、LangGraph adapter、Task/Run、Approval、Scheduler | schema versioned；节点类型/输入输出校验；条件/循环/并行/人工任务/定时触发可运行且有恢复测试 |
| P1 | 任务/会话工作台闭环 | 5 | Task/Run、Session/Asset、Approval、Artifact | Task/Session/Run/Approval/Artifact 单一详情链路；历史/流式消息、产物版本、人工接管和恢复可验收 |
| P1 | 模型与工具测试控制台 | 6 | Model Router、Tool Gateway、Secret Manager、Quota | 能做连接性/能力/Schema/权限/沙箱测试；密钥只显示引用；失败原因和审计可查 |
| P1 | 知识连接器与 ACL 撤销闭环 | 7 | Connector Sync、Knowledge ACL、Identity | 每个 connector 有支持范围/权限映射；源端撤权后索引、缓存、图谱和检索同步拒绝；有故障重试测试 |
| P1 | 知识/记忆质量与用户控制 | 7 | Knowledge Evaluation、Memory Governance、Audit | 可查看来源/版本/引用/质量；记忆可查看、纠正、删除、导出；敏感内容与 scope 策略有测试 |
| P2 | 行业 Solution Package 资产与交付中心 | 3 | Registry、Extension package、Connector、Knowledge、Evaluation | 可搜索行业包；声明 Agent/Workflow/Tool/Knowledge 依赖与参数 schema；安装预检、签名、升级/回滚和场景验收报告 |
| P2 | Agent 资产依赖图与运营分析 | 1、6 | Registry、Run/Trace、Cost/Evaluation | 可按版本查看依赖图、影响面、成功率/质量/成本；禁用依赖能提示受影响 Agent |
| P2 | 图谱与记忆跨域血缘 | 7 | Knowledge、Memory、Graph、Audit | 更新/撤权/删除可传播到图谱、记忆和 RAG；来源和版本可追溯 |

## 六、本轮自检与验证记录

- 基线：`main` @ `b622d5fc9eab1c79c28dcaa13367eec3c138679d`。
- 已检查：后端 API/领域模块/持久化仓库、相关 SQL migrations、前端路由/页面/CRUD 配置、专项测试文件、CI workflow 目录。
- 本轮专项测试命令：未执行。原因：当前连接器会话提供仓库源码读取/PR 操作，但没有本地仓库 checkout 和运行依赖；未启动 PostgreSQL、Redis、模型 provider、对象存储或前端 dev server。不得将测试文件存在写成测试通过。
- 真机验证：按调度要求跳过。
- CI：由本轮 PR 自动触发 GitHub Actions；最终状态按 PR Checks 回填。
- 代码改动：仅新增本专项审查报告；没有修改产品业务代码，符合 Issue #848 的 Code Audit 性质。
- 范围：仅 B2 产品中心 1–7；不处理 B1/C1 或其他产品中心；不合并任何 PR。

## 七、最终判断

- **保留并扩展**：Registry、Run/Task、Session/Asset、Blackboard、Tool Gateway、Knowledge/Memory/Graph。
- **优先补齐产品闭环**：Agent Test Bench、Workflow DSL/可视化画布、多 Agent 团队/委派配置、模型/工具测试台、任务会话工作台。
- **作为中长期独立资产能力建设**：行业 Solution Package，不应将普通文档模板或一组 Agent 配置包装成已完成行业应用中心。
- **避免重复建设**：Workflow 不另起一套执行 Runtime；多 Agent 不另造消息存储；知识/记忆不合并成无边界大仓库；模型/工具 UI 复用现有 Registry 和 Gateway。

本报告区分了代码机制、前端入口、测试文件和生产验收；未验证的真实 provider、生产数据源、跨实例执行与外部副作用恢复均不标记为已完成。
