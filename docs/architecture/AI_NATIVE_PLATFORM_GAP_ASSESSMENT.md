

## 30. C1 专项：十四个产品中心（8–14）逐项代码审查

### 30.1 审查边界与基线

- 专项范围：Issue #848 第八节“十四个产品中心”中的第 8～14 项；不扩展到其他编号任务。
- 代码基线：本轮读取的 `main`，commit `b622d5fc9eab1c79c28dcaa13367eec3c138679d`（2026-11-21）。
- 报告承载分支：`audit/issue-848-ai-native-gap-assessment`；本节是文档审查增量，不修改业务代码。
- 方法：核对前端页面/路由、API Router、生产组合根、领域服务/数据模型、迁移与针对性测试路径。文件路径存在只作为定位线索；没有在本轮运行的测试不标记为通过。
- 运行限制：当前执行环境未取得可用本地 checkout 和 PostgreSQL/Redis/MinIO 服务，本节不声称完成本地运行或真实企业环境验收。CI 结果以本次 PR 对应 GitHub Actions 实际检查为准。

成熟度沿用本报告定义：已完成、基础已完成、部分实现、仅有设计、尚未建设、待验证。对于证据不足的子功能，明确标记待验证，而不推断不存在。

### 30.2 七个产品中心成熟度总表

| 编号/产品中心 | 当前成熟度 | 已找到的主链路证据 | 核心缺失/风险 |
|---|---|---|---|
| 8. Agent 应用市场与资产中心 | 部分实现 | Registry API、租户范围 RegistryService、前端通用资产目录 CRUD；Extension Publisher 信任密钥注册/撤销；Skill Registry | 尚无证据证明形成面向企业/伙伴的市场目录、搜索发现、审核上架、授权安装、依赖解析、撤销传播、商业许可和资产消费计量的完整闭环 |
| 9. 企业组织与安全中心 | 基础已完成（组织/RBAC 基础）；部分实现（企业级身份治理） | Tenant/Principal/IdentityService、组织与访问控制 API、组织/RBAC 页面、服务身份和资源范围授权、审计接口 | 组织页面当前以租户/用户及角色/绑定/权限键为主；部门/团队层级、SSO/SCIM、组织生命周期、跨服务身份传播及全数据面隔离证据不完整 |
| 10. AI 治理与合规中心 | 部分实现 | Policy/Risk/Approval、Audit API/页面、脱敏、append-only audit 迁移、数据生命周期治理模块 | 缺统一合规控制面与可导出的合规报告、数据分类分级/保留/删除策略在各数据面的强制执行证据；审计不可篡改性受数据库运行角色配置影响 |
| 11. AI 质量评测中心 | 基础已完成（评测引擎）；部分实现（产品中心） | Evaluation Dataset/Runner/Store、指标比较、回归门禁证据、Rollout Bridge、前端评测洞察 | 开放式任务质量、人工标注运营、真实业务数据集代表性、生产发布路径的全量 Gate 强制接线、评测数据版本与权限治理仍需补齐 |
| 12. 运行监控与成本中心 | 基础已完成（可观测基础）；部分实现（运营闭环） | Trace/Span、Prometheus 指标、OperationalHealth、Alert Store/Worker/Dispatch、成本与质量页面、预算审计事件 | SLA/SLO 目标管理、告警升级/值班闭环、跨服务 Trace 完整性、成本来源准确性及企业/部门成本归集仍未整体验收 |
| 13. 企业资源与成本管理中心 | 部分实现 | Quota/Budget、层级策略、并发准入、Run Budget Ledger、Usage Reporting（按 model/project/agent 汇总）、Quota Admin API | 企业资源池/计算资源库存、部门预算审批与分摊、真实云资源账单对账、容量预测和成本中心会计维度未见完整闭环 |
| 14. 企业部署与运维管理中心 | 部分实现 | Production Compose、Health/Readiness、版本化部署状态机、升级脚本/manifest、PostgreSQL/Redis/对象存储备份恢复工具 | 单节点 Compose 不等于企业混合部署；控制面断网自治、HA/IaC、多环境配置漂移、全组件一致性恢复和真实升级/回滚演练仍待验证 |

### 30.3 8. Agent 应用市场与资产中心

**已实现边界**

- 后端入口：`agent_platform/api/registry.py` 的 `create_registry_router`，提供按 `kind` 列表、创建版本、读取 active 版本、发布及 disable 等注册表操作；路由在请求中解析 Principal，并将授权动作交给 authorize 回调，同时核对 repository tenant 与 Principal tenant。
- 领域层：`agent_platform/control_plane/registry.py`、`agent_platform/control_plane/skill_registry.py`；生产组合根 `agent_platform/production.py` 创建 PostgresRegistryRepository/RegistryService。
- 前端：`frontend/src/components/crud/RegistryCatalogPage.tsx` 是真实 API 驱动的通用目录组件，列表调用 `GET /platform/v1/registry/{kind}`，创建新版本并可发布，支持停用；路由配置包含 Agent、Skill、Tool 等管理入口。扩展发布者页面可登记/轮换/撤销发布者公钥。
- 测试线索：`tests/test_registry_listing_api.py`、`tests/test_registry_service.py`、`tests/test_registry_runtime_assembly.py`、`tests/test_skill_registry.py`、`tests/test_tool_registry_catalog.py`。

**逐功能判断**

| 功能点 | 判断 | 证据与缺口 |
|---|---|---|
| 资产注册/目录列表 | 基础已完成 | Registry API 与通用目录页存在；列表能力依赖 repository 的 list_items，缺少实现时 API 明确返回 501 |
| 资产版本与发布/停用 | 基础已完成 | 前端保存创建新版本并可调用 publish，支持 disable；完整版本比较、回滚策略和审批门禁待核验 |
| 资产分类、全文搜索、发现推荐 | 待验证 | 已读目录组件按 kind 拉取列表；未获得统一跨类型搜索、标签分类、推荐/评分服务的完整证据 |
| 依赖解析与兼容性检查 | 部分实现 | Extension 包有依赖/版本/签名信任机制；不能据此推断 Agent/Workflow/Prompt/Skill/Tool 全资产共享同一依赖图和兼容性约束 |
| 企业内部共享/伙伴授权/公共市场 | 待验证 | 未确认可见性范围、伙伴租户、授权合同、安装目标环境及撤销传播的完整服务端链路 |
| 上架审核、安装、升级、卸载 | 部分实现 | Extension Manager 有扩展包生命周期；未证明一般 AI 资产具备审核→签名→分发→安装→升级→回滚→撤销端到端流程 |
| 商业化与消费计量 | 待验证 | Quota/usage 计量是平台资源成本基础，不等同资产订阅、许可授权、收入分成与结算 |

**缺失功能/建议**

1. 统一 Asset Manifest：类型、稳定 ID、版本、依赖锁、兼容平台版本、权限声明、数据访问声明、来源/签名、许可证、可见性和维护者。
2. Marketplace 服务端目录：分类/标签/全文搜索、筛选、版本比较、风险/兼容状态和审计记录。
3. 安装授权流程：企业管理员审批、目标租户/环境绑定、依赖解析、签名/漏洞扫描、安装前权限差异预览、安装回执和卸载清理。
4. 伙伴资产信任边界：内部私有、企业共享、指定伙伴、公共目录四类可见性；撤销密钥/资产后阻止新安装并定义已安装副本处置策略。
5. 增加跨资产端到端测试，覆盖越权列举、伪造 tenant、版本冲突、撤销后安装/运行拒绝、升级失败回滚和依赖供应链风险。

优先级：P1（资产发布安全与授权闭环）；P2（市场搜索、伙伴门户、商业化和评分运营）。

### 30.4 9. 企业组织与安全中心

**已实现边界**

- 身份/租户：`agent_platform/identity/models.py`、`service.py`、`postgres.py`、`tenants.py`、`resource_scope.py`；`agent_platform/api/identity.py` 提供登录/初始化入口；`agent_platform/api/app.py` 组装身份与认证中间件。
- 管理 API：组织与访问控制由 admin 路由提供；前端 `frontend/src/components/OrganizationAccessPage.tsx` 请求 `/platform/v1/admin/organization` 或 `/platform/v1/admin/access-control`，展示租户/用户、角色/角色绑定/角色权限。
- 页面入口：`frontend/src/routes/route-config.ts` 中 `/admin/organization`、`/admin/access-control` 标记 `authOnly: true`。
- 机器身份：`identity/service_accounts.py`、Service Credential 相关 API/测试；资源授权：`identity/resource_scope.py`。
- 测试线索：`tests/test_identity.py`、`test_identity_api.py`、`test_identity_resource_scope.py`、`test_service_identity.py`、`test_service_identity_authorization_integration.py`、`test_tenant_creation_integration.py`、`frontend/e2e/organization-access.spec.ts`。

**逐功能判断**

| 功能点 | 判断 | 证据与缺口 |
|---|---|---|
| Tenant/用户/登录会话 | 基础已完成 | 有 Principal、身份服务、PostgreSQL Repository、登录与租户初始化 API；生产部署真实身份源/SSO 仍待验证 |
| 组织/部门/团队层级 | 部分实现 | 页面/路由描述组织、部门、项目，但已读页面数据主要是 tenants/users；尚未证明完整部门树、团队嵌套、继承规则和生命周期 |
| RBAC 角色与权限绑定 | 基础已完成 | access-control API 和 UI 展示角色、绑定、权限键；批量授权、策略模拟、权限变更审批和完整审计需进一步验证 |
| 资源级授权/租户隔离 | 基础已完成（局部链路） | ResourceScopeAuthorizer 与跨租户测试存在；Outbox、Worker、Connector、对象存储、缓存、向量检索等全路径隔离尚未形成统一验收矩阵 |
| SSO/企业目录同步 | 待验证 | 本轮未获得 OIDC/SAML/SCIM 完整企业身份联邦与自动入离职同步链路证据 |
| 数字员工组织身份 | 尚未建设（在已审查范围内未找到独立资源闭环） | 既有 Agent 资源不能代替数字员工岗位、主管、职责、组织成员关系和绩效身份 |
| 组织安全运营 | 部分实现 | 有身份事件、审计、服务身份和凭据机制；缺少完整组织生命周期管理、访问复核、离职自动撤权和异常身份风险运营闭环 |

**缺失功能/建议**

- 建立 Organization/Department/Team/Position/Member 关系及层级授权规则；为组织资源定义稳定 ID、tenant 归属、有效期和历史变更记录。
- 补 OIDC/SAML、SCIM、企业目录映射、MFA/条件访问（按企业策略选配），并定义本地 Owner 紧急访问与 IdP 故障降级。
- 建立统一访问策略模拟/解释 API，支持用户/服务身份/数字员工对资源动作的决策原因、继承来源和有效权限预览。
- 将租户隔离测试扩展到 Worker、Outbox、Connector、对象存储、缓存、向量库和审计读取；身份撤销后验证 queued/resumed Run 不能沿用旧授权。
- 单独建设 DigitalEmployee 领域资源，不将 Agent ID 当作组织身份。

优先级：P0（执行身份与异步任务权限复核、数据面隔离）；P1（组织层级/SSO/SCIM/访问复核）；P2（数字员工组织运营）。

### 30.5 10. AI 治理与合规中心

**已实现边界**

- 治理基础：`agent_platform/approval/policy.py`、`approval/service.py`、`agent_platform/graph/governance.py`、`agent_platform/data_governance/`、`agent_platform/security/audit.py`。
- 审计 API：`agent_platform/api/audit.py` 提供工具事件、身份事件和 Run 预算事件只读查询；请求必须有 Principal 并通过 authorize_read；结果执行 redact_payload。
- 前端：`frontend/src/features/audit/AuditCenterPage.tsx` 展示身份、工具、Run 预算事件；`AuditEventDetailPage.tsx` 提供事件详情。
- 数据治理模块包含生命周期、迁移审计、导出/差异/切换辅助代码；迁移包含 append-only audit 约束。相关测试：`tests/test_audit_api.py`、`test_audit_append_only_postgres.py`、`test_data_lifecycle_governance.py`、`test_data_lifecycle_audit_migration.py`、`test_graph_governance.py`。

**逐功能判断**

| 功能点 | 判断 | 证据与缺口 |
|---|---|---|
| 风险分级、审批与策略执行 | 基础已完成 | RiskLevel/Approval/Policy 代码存在；仍需证明所有生产 Runtime/Connector/业务副作用均不可绕过统一策略 |
| 身份/工具/预算审计 | 基础已完成 | 多类只读审计 API 和审计页面存在；数据源缺失时 API 可返回 503，需确保生产必需数据源均装配 |
| 敏感数据脱敏 | 部分实现 | 审计响应使用 redact_payload；未证明日志、Trace、Prompt、附件、导出、备份和外部遥测统一执行脱敏策略 |
| 数据分类分级与保留 | 部分实现 | data_governance 生命周期/审计模块存在；尚缺分类标签、自动识别、策略覆盖矩阵和各存储类型删除传播证明 |
| 合规报告与责任追踪 | 待验证 | 有审计事件和数据治理证据；未获得按控制项生成、导出、签名、周期复核的合规报告产品闭环 |
| 审计不可篡改 | 部分实现/存在部署前提风险 | append-only migration 限制 runtime DB role；此前审查确认 production Compose/API/Worker 共享 POSTGRES_USER，与最小权限前提冲突，不能据此声称生产审计账本不可篡改 |
| 策略例外/审批留痕 | 部分实现 | 审批/审计组件存在；例外到期、策略版本快照、策略变更审批和周期复核需补证 |

**缺失功能/建议**

- 定义企业合规控制目录（控制项、适用数据类别、责任人、证据来源、复核周期、例外到期时间）。
- 统一数据分类标签与访问策略，覆盖数据库、向量、文件/对象存储、缓存、日志、Trace、导出和备份。
- 将审计 DB owner、API/Worker runtime、audit writer/reader 分离，增加 Release Gate 检查角色属性、表 owner、ACL、DDL/TRIGGER 权限。
- 建立合规证据导出与完整性验证流程，并对证据读取本身审计；明确保留期、删除例外和法律保全策略。
- 建立策略决策日志与审批绑定，记录策略版本、主体、资源、动作、风险、审批人、执行结果及副作用引用。

优先级：P0（审计权限边界与策略强制执行）；P1（数据分类/生命周期/合规证据）；P2（法规模板与自动化合规运营）。

### 30.6 11. AI 质量评测中心

**已实现边界**

- 后端：`agent_platform/observability/evaluation_datasets.py`、`evaluation_runner.py`、`evaluation_store.py`、`evaluation_comparison.py`、`evaluation_governance.py`、`evaluation_rollout.py`；知识检索另有 `agent_platform/knowledge/evaluation_dataset.py`、`knowledge/evaluation.py`。
- API：`agent_platform/api/observability.py` 中注入评测 Repository、Dataset Repository、Run Service、Governance Service、Comparison Service；`agent_platform/api/evaluation_rollout.py` 将实验结果接入 Prompt rollout，检查 Principal、tenant、publish 权限并写审计。
- 前端：`/operations/evaluation/datasets`、`/operations/evaluation/runs`、`/operations/evaluation/insights` 路由；`frontend/src/features/evaluation/EvaluationInsightsPage.tsx`。
- 测试线索：`tests/test_evaluation_datasets.py`、`test_evaluation_runner.py`、`test_evaluation_comparison.py`、`test_evaluation_governance.py`、`test_evaluation_rollout.py`、`test_evaluation_worker.py`、`test_knowledge_evaluation_reproducibility.py`。

**逐功能判断**

| 功能点 | 判断 | 证据与缺口 |
|---|---|---|
| 数据集管理/版本 | 基础已完成 | Dataset Repository/tenant version migration/页面入口存在；标注协作、数据集导入导出和敏感样本治理需验证 |
| 自动评测运行 | 基础已完成 | Runner/Worker/Store 具备运行机制；真实模型、企业业务样本及大规模并发运行未在本轮执行 |
| 指标与质量维度 | 部分实现 | 支持 pass rate、score、retrieval、citation、latency、cost 等指标；指标有效性与开放任务主观质量仍依赖数据集/评审设计 |
| 人工标注/专家复核 | 待验证 | 没有充分证据证明标注分工、仲裁、标注员一致性和审计工作台形成完整闭环 |
| 版本对比/回归分析 | 基础已完成 | evaluation_comparison 与 insights 页面有基线/候选对比；需验证不同模型/Prompt/Agent/Workflow 快照可复现 |
| 发布门禁 | 部分实现 | EvaluationGateEvidence 与 Prompt rollout bridge 存在；此前报告确认 Prompt Evaluation Gate 未接入默认生产组合的证据不足，不能宣称所有资产发布都强制 fail-closed |
| 反馈闭环 | 部分实现 | 具备反馈/评测数据基础；生产反馈采集到候选修复、复测、人工审批和发布的闭环未充分证明 |

**缺失功能/建议**

- 建立覆盖 Agent、Workflow、Prompt、模型、检索、工具调用和端到端任务的标准数据集格式，记录数据来源、许可、敏感级别、版本和抽样策略。
- 增加人工标注工作台：标注任务分派、双人标注、分歧仲裁、质量抽检和标注员一致性统计。
- 评测结果绑定不可变的模型/Prompt/Agent/Workflow/Tool 版本快照、运行配置、数据集版本和评测器版本。
- 将 Gate 作为发布服务端硬约束：没有匹配候选版本的有效证据、证据过期或指标回归时拒绝发布；覆盖所有资产发布入口。
- 增加真实模型/企业语料评测与开放式任务人工评分，避免仅以固定分数/合成数据得出产品质量结论。

优先级：P1（Gate 生产接线与版本快照）；P2（人工标注运营、真实任务质量和持续反馈闭环）。

### 30.7 12. 运行监控与成本中心

**已实现边界**

- 后端：`agent_platform/observability/run_trace.py`、`trace_context.py`、`trace_spans.py`、`instrumentation.py`、`metrics.py`、`prometheus.py`、`operational_health.py`、`alerts.py`、`alert_policy.py`、`alert_store.py`、`alert_dispatch.py`、`alert_worker.py`、`cost.py`。
- API：`agent_platform/api/observability.py` 提供 metrics、告警、评测相关端点；`agent_platform/api/audit.py` 提供 Run 预算审计事件。
- 前端：`/operations/alerts`、`/workspace/quality-cost`；`AlertsPage.tsx`、`CostQualityCenter.tsx`；路由说明运行状态、告警和成本质量入口。
- 测试线索：`tests/test_run_trace.py`、`test_trace_context.py`、`test_trace_spans.py`、`test_observability.py`、`test_alert_policy.py`、`test_alert_dispatch.py`、`test_alert_worker.py`、`test_issue_16_a_16_03_alert_worker_composition.py`、`test_issue_26_a_26_02_metric_accuracy.py`。

**逐功能判断**

| 功能点 | 判断 | 证据与缺口 |
|---|---|---|
| Run/模型/工具 Trace | 基础已完成 | 有 Trace Repository、上下文传播和 Span 模型；跨进程/异步 Worker/外部 Connector 端到端关联仍需验证 |
| Metrics/健康状态 | 基础已完成 | Prometheus recorder、metrics API、OperationalHealthMonitor 存在；指标覆盖与 readiness 依赖完整性需核验 |
| 告警规则/投递/处置 | 基础已完成 | Alert Policy/Store/Worker/Dispatch 和前端确认/解决入口存在；值班升级、通知渠道故障转移、静默窗口和复盘流程需补证 |
| Token/模型/运行成本 | 部分实现 | cost 与 budget ledger/reporting 存在；估算成本、Provider 实报、reconciled 来源需清晰区分，不能把估算当真实账单 |
| SLA/SLO 与容量 | 待验证 | 有健康检查、指标和策略，但没有充分证据证明产品化 SLO 目标、错误预算、容量预测、服务等级报表和 SLA 违约事件闭环 |
| 运营报表 | 部分实现 | 成本质量中心与告警页存在；面向企业管理者的可配置时间范围、组织维度、导出、趋势和业务影响归因需进一步核验 |

**已确认的成本口径约束**

`agent_platform/quotas/usage_reporting.py` 明确分开 estimated、provider_reported、reconciled 三类 cost source；tool_calls、runtime_ms、storage_bytes 作为独立资源计数，不虚构其货币成本。该设计是正确的口径边界，但还需核验 Provider 实报接入覆盖率、对账任务和页面是否完整呈现来源与时间窗口。

**缺失功能/建议**

- 统一 Trace Context 贯穿 API、Run、Worker、Outbox、模型、工具、Connector 和 Webhook；提供跨组件 Trace 查询与采样策略。
- 增加服务/业务 SLO、错误预算、延迟分位数、成功率、队列积压和恢复时间目标；告警支持升级、认领、值班路由、静默、抑制与复盘。
- 成本报表显示估算/Provider 实报/对账金额、计费币种/费率版本、时间窗口、归属来源和数据新鲜度。
- 补齐企业/部门/团队/Agent/项目维度成本归集与预算偏差；业务价值指标需明确估算方法与可信等级。

优先级：P1（Trace/告警可靠性与成本来源准确性）；P2（SLO、容量与业务价值运营）。

### 30.8 13. 企业资源与成本管理中心

**已实现边界**

- 配额模块：`agent_platform/quotas/` 包含 admission、concurrency、groups、inheritance、policies、postgres、pricing、run_ledger、storage_meter、usage_reporting、token_estimator 等。
- API：`agent_platform/api/quota_admin.py` 提供租户范围预算管理；`agent_platform/api/quotas.py` 提供用量报告；权限检查 tenant scope 必须与 Principal tenant 一致，非 tenant scope 需 authorize_scope。
- 数据：迁移含 tenant quota/cost、层级配额策略、reservation lease、Run budget ledger 等，例如 `016_tenant_quotas_cost.sql`、`071_hierarchical_quota_policies.sql`、`049_run_model_call_budget_ledger.sql`（注意迁移编号存在重复前缀，参见前文迁移审查）。
- 前端：`/admin/settings` 路由描述配额、预算上限、并发限制和存储统计；`/workspace/quality-cost` 展示成本质量。
- 测试线索：`tests/test_quota_admin_api.py`、`test_quota_policies.py`、`test_quota_admission_bounds.py`、`test_quota_concurrency.py`、`test_quota_usage_reporting.py`、`test_quota_usage_reporting_api.py`、`test_quota_postgres_integration.py`、`test_issue_17_a_17_03_production_quota_wiring.py`。

**逐功能判断**

| 功能点 | 判断 | 证据与缺口 |
|---|---|---|
| Token/模型调用预算 | 基础已完成 | Token estimator、Run ledger、预算预留/结算和模型价格目录存在；实际 Provider 账单覆盖和费率更新需验证 |
| 租户/层级配额策略 | 基础已完成 | hierarchical policies/inheritance/groups 存在；组织层级与部门/团队预算授权的产品闭环仍需验证 |
| 并发与准入控制 | 基础已完成 | concurrency/admission/reservation leases 有模块与测试；多实例压力和故障恢复未在本轮运行 |
| 部门预算、成本分摊 | 部分实现 | Usage Reporting 可按 project/agent/model 汇总；未证明部门成本中心、分摊规则、内部转移价格和预算审批工作流 |
| 计算/存储资源池 | 部分实现 | 存储计量和 runtime_ms/tool_calls 资源计数存在；未见可验收的 GPU/CPU/内存/容器池库存、调度和容量预留产品 |
| 资源成本对账 | 部分实现 | usage report 分开成本来源；云资源/存储/第三方系统账单导入、校验、差异处理未证明完整 |
| 预算预警与超额治理 | 部分实现 | quota runtime alerts/alert routes 有代码；预算周期预测、部门级预警升级、超额申请与审批流程需补证 |

**缺失功能/建议**

- 建立 ResourcePool/ResourceReservation 领域模型，覆盖 CPU、内存、GPU、并发槽位、存储、网络和运行时额度，定义容量、区域、租户归属和故障域。
- 统一 Cost Center：Tenant→Organization/Department→Project→Agent/Run 的分摊维度；分摊规则版本化且可重算。
- 接入 Provider/云平台/对象存储账单，保留原始账单行、费率版本、币种、税费和对账状态；明确平台估算与供应商实报差异。
- 建立预算申请/审批/调整/冻结/超额授权/周期复核流程，并与 Policy/Approval/Audit 统一关联。
- 提供预算预测、用量趋势、单位任务成本、闲置资源和容量规划报表。

优先级：P1（实际用量/成本来源、租户预算边界）；P2（部门分摊、资源池、云账单对账和容量规划）。

### 30.9 14. 企业部署与运维管理中心

**已实现边界**

- 生产部署：`deploy/production.compose.yaml`，包含 API、Worker、PostgreSQL、Redis、对象存储等服务定义及容器安全/健康检查配置。
- 部署与升级：`agent_platform/deployment/versioned.py` 定义 rolling/blue-green/canary/gray 策略、阶段状态、健康门槛和自动回滚决策；`scripts/release_upgrade.py`、`scripts/release_upgrade.sh`、`docs/deployment/release-upgrade.md` 描述版本化升级。
- 备份恢复：`agent_platform/deployment/backups.py` 定义 BackupPolicy、BackupArtifact、BackupCatalog 和 restore drill 状态；`scripts/backup_platform.sh`、`restore_platform.sh` 及 PostgreSQL/Redis/对象存储分项脚本；`deploy/BACKUP_POLICY.md`、`docs/deployment/backup-disaster-recovery.md`。
- 测试线索：`tests/test_deployment_compose.py`、`test_versioned_deployment.py`、`test_release_upgrade.py`、`test_backup_disaster_recovery.py`、`test_backup_recovery_scripts.py`、`test_issue_18_a_18_02_backup_restore_drill.py`、`test_production_deployment_contract.py`。

**逐功能判断**

| 功能点 | 判断 | 证据与缺口 |
|---|---|---|
| 企业实例/环境配置 | 部分实现 | 有生产 Compose 和环境样例；多环境配置版本化、漂移检测、实例注册与企业级配置中心需验证 |
| 版本发布/升级/回滚 | 基础已完成（状态机/脚本） | 有策略状态机和升级脚本；真实集群流量切换、数据库兼容窗口、失败回滚演练需验证 |
| 健康检查/运行诊断 | 基础已完成 | health/readiness、Prometheus、告警模块存在；依赖故障注入和诊断自动化覆盖不足 |
| 备份/恢复 | 基础已完成（工具/策略）；部分实现（完整平台灾备） | PostgreSQL/Redis/对象存储备份恢复工具与完整性校验存在；此前审查确认真实 CLI 演练未覆盖全平台跨组件一致性、failover/failback |
| 容量/弹性/高可用 | 待验证 | 当前生产工件以单节点 Compose 为主；没有充分 IaC/Kubernetes/多 AZ/自动扩缩容及 HA 运行证据 |
| 混合部署/离线自治 | 待验证 | 代码和文档有企业部署目标，但没有公共控制平面断网时企业核心身份、任务、知识、密钥和审计持续工作的可复现证明 |
| 许可证/离线授权 | 待验证 | 本轮没有确认许可证签发、离线验证、续期宽限、吊销缓存和企业离线启动完整链路 |

**缺失功能/建议**

1. 提供企业部署拓扑模板（自有云/私有云/本地），明确控制平面、运行平面、数据平面和生态服务网络边界；提供 IaC/Helm/Kubernetes 或等价可复现安装工件。
2. 建立 Instance/Environment/Release/ConfigRevision 资源模型，支持多环境配置差异、密钥引用、漂移检测、变更审批和回滚。
3. 将数据库迁移、应用版本、资产版本、配置版本和备份恢复点纳入同一发布记录；升级前自动备份与兼容性检查，升级后健康门禁，失败自动/人工回滚。
4. 补完整平台灾备：PostgreSQL、Redis、对象存储、配置、密钥、许可证和外部依赖一致性；验证异地副本、RPO/RTO、failover/failback 和定期恢复演练。
5. 混合自治验收：切断公共控制面和互联网后，验证企业侧已授权 Agent/Workflow、身份权限、任务调度、知识检索、密钥使用、审计和本地运维；恢复连接后安全同步。
6. 建立运维中心页面：实例列表、环境状态、版本差异、升级计划、备份目录、恢复演练、容量、告警、诊断包和审计记录。现有 Compose/脚本不等同于完整运维产品中心。

优先级：P0（生产升级/凭据/备份恢复安全门禁）；P1（HA、完整灾备、环境管理和混合部署验收）；P2（离线许可、统一运维门户和多环境规模化）。

### 30.10 跨中心端到端断点与验收矩阵

| 跨中心链路 | 已有组件 | 尚未闭合的端到端证据 | 建议验收 |
|---|---|---|---|
| 市场资产→租户安装→运行授权 | Registry、Publisher Key、Identity/Resource Scope、Runtime Registry Snapshot | 市场目录/伙伴授权、安装回执、撤销传播、运行时权限快照的一致性 | 不同租户/伙伴角色尝试搜索、安装、升级、运行和撤销；验证越权拒绝及审计 |
| 组织身份→策略→审批→审计 | Identity、RBAC、Approval、Audit | 部门继承、服务身份、异步 Worker 执行时重新授权、DB runtime 角色分离 | 撤销用户/服务身份后 queued/resumed Run 与工具副作用必须拒绝；审计写入成功且不可由 runtime 修改 |
| 评测→发布门禁→灰度→回滚 | Dataset、Runner、Comparison、Gate Evidence、Rollout Bridge、Deployment State Machine | Gate 与所有资产发布入口是否统一强制、证据是否绑定不可变版本、回滚是否可复现 | 缺证据/过期证据/指标回归 fail closed；通过后灰度，指标恶化自动回滚并留审计 |
| Trace→成本→预算→告警 | Run Trace、Metrics、Usage Ledger、Quota、Alert | trace_id 到账单行、项目/部门归集、告警处置的全链路关联 | 注入真实模型用量和成本回执，校验 ledger、部门报表、预算扣减、告警与审计一致 |
| 部署版本→配置→备份→恢复 | Versioned Deployment、Upgrade Scripts、Backup/Restore | 应用/DB/对象存储/配置/密钥版本一致性及完整平台恢复 | 在隔离环境执行升级失败回滚、跨组件恢复、异地恢复和 RPO/RTO 计时 |

### 30.11 C1 去重后的研发缺口 Backlog

| ID | 优先级 | 缺口/任务 | 依赖 | 可验证验收标准 |
|---|---|---|---|---|
| C1-08-01 | P1 | 统一 AI Asset Manifest、依赖锁、签名/扫描、资产发布与撤销协议 | Registry、Publisher Trust、Identity | Agent/Workflow/Skill/Tool/Prompt 统一版本；无签名/权限超范围/依赖冲突的资产拒绝安装和发布 |
| C1-08-02 | P2 | Marketplace 搜索、分类、企业/伙伴/公共目录及授权安装 | C1-08-01、Tenant/Org | 跨租户目录可见性测试、安装/卸载/撤销回归和完整审计通过 |
| C1-09-01 | P0 | Worker/异步恢复执行时主体身份与策略复核 | Identity、Runtime、Policy | 身份撤销后排队、重试、恢复任务均 fail closed；覆盖 PG/Redis 集成测试 |
| C1-09-02 | P1 | 组织层级、部门/团队继承、SSO/SCIM 与访问复核 | Identity、Tenant、Audit | 创建/同步/调岗/离职端到端测试；离职身份不能访问历史敏感资源 |
| C1-09-03 | P2 | DigitalEmployee 独立组织身份和岗位责任模型 | Organization、Agent、Task | 数字员工具备岗位、主管、职责、授权、任务、绩效与停用生命周期，不与 Agent 混同 |
| C1-10-01 | P0 | 审计数据库角色隔离与强制策略链路 | DB roles、Runtime、Approval | Release Gate 证明 API/Worker 无审计表 owner/DDL 权限；策略绕过负向测试通过 |
| C1-10-02 | P1 | 数据分类分级、保留/删除传播与合规证据报告 | Data Governance、Storage、Audit | DB/向量/文件/缓存/日志/备份各层数据策略一致；可导出可验证的合规证据包 |
| C1-11-01 | P1 | 将评测 Gate 强制接入所有资产发布入口 | Evaluation、Registry、Rollout | 无有效版本绑定证据或存在回归时拒绝发布；通过后灰度/回滚有审计 |
| C1-11-02 | P2 | 人工标注、真实业务评测与反馈闭环 | Dataset、Evaluation、Governance | 标注分派/仲裁/一致性、真实语料基准、人工审批后发布全流程验收 |
| C1-12-01 | P1 | Trace 跨 API/Worker/Connector 与成本账单关联 | Trace、Outbox、Usage Ledger | 同一 trace/run 可追溯模型、工具、成本、预算、告警及审计事件 |
| C1-12-02 | P2 | SLO/SLA、值班升级、容量和业务价值运营 | Metrics、Alert、Org | 可配置 SLO、错误预算、告警升级/复盘及按部门业务价值报表验收 |
| C1-13-01 | P1 | Provider/云资源成本真实账单接入与对账 | Usage Ledger、Connector、Quota | 原始账单可追溯，估算/实报/对账金额分开，差异可复核 |
| C1-13-02 | P2 | 部门 Cost Center、资源池、预算审批与容量预测 | Organization、Quota、Approval | CPU/GPU/存储/并发资源池可分配；部门分摊可重算，预算审批与告警审计闭环 |
| C1-14-01 | P0 | 生产升级、迁移、密钥和恢复的发布门禁 | Deployment、Secrets、Backup | 迁移兼容性/备份验证失败时阻断升级；失败回滚恢复一致性并留下证据 |
| C1-14-02 | P1 | 企业 HA/IaC、完整灾备和混合部署断网自治 | Deployment、Backup、Hybrid Control Plane | 隔离环境完成 HA 故障切换、跨组件恢复、断网自治和 RPO/RTO 演练 |
| C1-14-03 | P2 | 实例/环境/版本/容量/备份统一运维门户 | Deployment API、Observability、Identity | 运维角色可完成实例巡检、环境差异、升级、回滚、备份恢复与审计 |

### 30.12 本轮结论与验证状态

- C1 的七个产品中心均找到部分后端/数据/前端或测试基础，不应笼统判为“没有建设”；但除身份、评测、监控、配额等底层机制外，多数仍未形成完整的产品化闭环。
- 主要 P0 风险集中在执行身份复核、审计数据库权限与生产升级/恢复门禁；P1 集中在市场资产安全发布、组织治理、评测 Gate、成本账单对账和企业 HA/灾备；P2 集中在伙伴市场、数字员工、人工标注、部门资源成本和统一运维门户。
- 本轮为源码静态审查增量；已核对 main 的最新 SHA、前端页面/路由、API、领域模块、迁移/测试路径。没有执行本地测试、构建或真机/真实服务器验证；不可将测试文件存在等同于测试通过。
- 本节覆盖 C1 指定的产品中心 8～14；不代表 Issue #848 全部目标能力或全部验收标准已经完成。Issue 应保持 Open，PR 保持 Draft，等待其余审查工作包与环境验证。

