# D1 十项战略扩展能力专项代码审查

- Issue：[#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- 工作包：D1 — 十项战略扩展能力逐项审查
- 唯一代码基线：main @ b622d5fc9eab1c79c28dcaa13367eec3c138679d
- 审查日期：2026-11-21
- 范围：仅审查 Issue #848 第八节“十项战略扩展能力”；不修改产品代码，不申请真实服务器或外部资源。
- 方法：GitHub Tree API 枚举基线文件路径；读取相关服务、API、生产装配、前端路由、测试与架构契约。文件路径存在不作为能力完成证据；测试源码存在不等于测试已执行。

## 1. 结论摘要

| # | 战略能力 | 成熟度 | 本轮判断 |
|---|---|---|---|
| D1-01 | 企业 AI 决策中心 | 待验证（未发现专属实现） | 有评测、告警、图谱、审批等可复用数据/治理积木，但未形成经营指标分析、预测、决策方案比较及审批执行闭环 |
| D1-02 | AI 数字员工管理中心 | 尚未找到有效实现证据 | 有人类用户、Agent、任务与权限模型；未发现独立 DigitalEmployee 组织身份、岗位职责、主管关系、绩效及生命周期模型 |
| D1-03 | 企业流程挖掘中心 | 尚未找到有效实现证据 | 有事件基础设施和工作流运行记录；未发现事件日志标准化、流程发现、合规/偏差分析、瓶颈识别和流程优化度量链路 |
| D1-04 | AI 自主优化中心 | 部分实现 | Prompt 版本、A/B 实验、评测与灰度路由存在；未形成覆盖失败归因、纠正反馈、候选变更生成、回归验证、审批发布和生产效果回滚的统一闭环 |
| D1-05 | 企业知识图谱中心 | 基础已具备 | 有抽取候选、来源血缘、置信度/冲突处理、人工确认、治理、查询 API 与前端入口；领域覆盖、实体关系质量和生产级数据接入仍待验证 |
| D1-06 | 多模态 AI 中心 | 部分实现（运行时契约基础） | Harness 有 image/pdf/audio/video 能力声明及媒体引用安全约束；未发现可证明的 OCR、ASR/TTS、视频理解、图纸理解及生产模型适配完整链路 |
| D1-07 | AI 事件驱动中心 | 基础已具备（基础设施层） | PostgreSQL Outbox、Redis Streams、receipt、pending reclaim、DLQ、Webhook 与调度入口存在；事件目录、业务规则编排和业务副作用 exactly-once 尚未统一闭环 |
| D1-08 | 企业 AI 合同与服务管理中心 | 尚未找到有效实现证据 | 有通用 API 契约、配额和预算能力；未发现合同、套餐、模块许可、期限/续费、离线授权和结算领域模型 |
| D1-09 | AI 业务连续性管理中心 | 部分实现 | 有备份策略、恢复编排/校验、RPO/RTO 记录与演练模板；真实自动故障切换、完整跨组件一致性恢复和实测演练未获证实 |
| D1-10 | 企业 AI 创新实验室 | 待验证（未发现专属产品闭环） | Prompt/Workflow/RAG 实验、评测和沙箱基础可复用；未发现自然语言建应用、创新项目治理、沙箱成果验收及转生产闭环 |

**总体判断：** 十项战略能力并非全部从零开始。D1-05、D1-07 已有可复用的技术底座；D1-04、D1-06、D1-09 已有局部机制；D1-01、D1-02、D1-03、D1-08、D1-10 尚无足够证据证明形成独立能力。不得把通用组件、实验性代码、页面导航或设计契约等同于产品中心交付。

## 2. 逐项审查

### D1-01 企业 AI 决策中心

**成熟度：待验证（未发现专属实现）。**

- 已有可复用基础：
  - agent_platform/observability/evaluation.py、evaluation_comparison.py、evaluation_datasets.py 提供评测与对比能力；
  - agent_platform/observability/operational_health.py（生产组装引用）和告警相关服务提供运行健康/异常信号；
  - agent_platform/graph/query.py、agent_platform/api/graph.py 提供知识图谱查询入口；
  - agent_platform/approval/service.py 提供审批基础。
- 前端已有 /workspace/quality-cost、/operations/alerts、/operations/knowledge-graph、/operations/evaluation/insights 等相邻入口（frontend/src/routes/route-config.ts），但没有发现企业经营决策中心专属路由。
- 未获得实现证据的子功能：经营指标体系/指标口径管理、经营数据汇聚与血缘、趋势/异常/风险预测、决策方案生成及多方案量化比较、证据/数据来源可追溯的决策记录、决策审批与执行结果反馈。
- 风险：若仅把告警或评测报表命名为决策中心，会缺少业务指标语义、预测可信度、方案影响评估及决策责任链。
- 建议：P2 新建决策领域能力前，复用 Evaluation、Graph、Trace、Approval；先定义 KPI/Metric、DecisionCase、Evidence、Option、DecisionApproval、Outcome 资源与可追溯数据契约，再建设指标与方案闭环。

### D1-02 AI 数字员工管理中心

**成熟度：尚未找到有效实现证据。**

- 已有基础：agent_platform/identity/models.py 定义 UserRecord、Principal、AuthorizationContext 等人类身份/授权对象；agent_platform/production.py 具备 Registry、Task、Run、Approval 等服务装配；Agent 定义页面存在。
- 关键缺口：当前身份模型是人类用户模型，没有 DigitalEmployee 独立资源；路由配置有组织成员、Agent 定义和任务中心，但未发现数字员工管理路由或页面。
- 未获得实现证据的子功能：数字员工独立身份类型；企业/部门/团队/岗位归属；岗位职责、服务范围与目标；人类负责人/主管/审批人；Agent/Workflow/Skill/Tool/知识绑定；执行级别、额度、时段及风险边界；上岗、停用、调岗；绩效、成本、交付物和接管记录。
- 风险：把 Agent 直接改名为数字员工会混淆“执行资产”和“组织责任主体”，无法进行岗位授权、主管监督、人员交接及绩效归属。
- 建议：P2 建立 DigitalEmployee 与 User、Organization、Agent、Task、Run、Approval 的明确关系；权限由服务端授权策略决定，不允许 Agent 配置自行声明组织权限。

### D1-03 企业流程挖掘中心

**成熟度：尚未找到有效实现证据。**

- 已有可复用基础：agent_platform/events/outbox.py、events/consumer.py、streaming/run_events.py 提供事件投递/消费与运行事件；Run/Workflow 运行状态与 Trace 可作为部分事件来源。
- 本轮文件树未发现 process-mining 专属模块、API、迁移、前端页面或测试。事件总线只证明事件传递基础，不等于具备流程挖掘。
- 未获得实现证据的子功能：ERP/CRM/OA/MES 等业务事件日志接入与标准化；Case ID/Activity/Timestamp/Actor 等事件语义；流程自动发现与变体聚类；标准流程与实际流程偏差/合规检查；瓶颈、返工、等待与人工重复操作分析；自动化机会识别；优化前后周期/成本/质量效果对比。
- 风险：缺少跨业务系统统一 Case/Activity 语义时，无法重建真实端到端流程；仅分析 Agent 自身 Run 事件会产生严重的业务流程覆盖偏差。
- 建议：P2 先确定事件日志数据契约和连接器范围，再建设 ProcessModel、ProcessCase、ProcessEvent、ConformanceFinding、Bottleneck、Opportunity 等领域模型与分析 API。

### D1-04 AI 自主优化中心

**成熟度：部分实现。**

- 代码证据：
  - agent_platform/observability/prompt_experiments.py：PromptABTestService.run() 对租户范围内活动 Prompt 变体执行数据集实验，控制并发/超时，记录 Case Score 与实验报告；PostgreSQL Repository 持久化报告。
  - agent_platform/observability/prompt_rollouts.py：PromptRolloutService.configure()/route()/auto_promote()/manual_cutover()/audit() 支持路由策略、灰度决策、自动晋级入口、人工切换和审计。
  - tests/test_prompt_experiments.py 覆盖变体执行/比较、租户范围、超时、评分校验与 PostgreSQL Repository（测试源码存在；本轮未执行）。
  - tests/test_prompt_rollouts.py（文件存在）覆盖灰度发布相关行为，仍需结合 CI 结果核验。
- 已实现边界：Prompt 变体实验、评测分数/报告、灰度路由与发布操作基础；不能据此认定 Agent/Workflow/Tool/Policy 的自主优化闭环已实现。
- 未获得实现证据的子功能：生产失败自动归因与分类；用户纠正/人工反馈汇聚；自动生成候选 Prompt/配置修复；自动生成回归测试用例；跨模型/任务集质量与成本权衡；候选版本与权限/安全策略的联动评估；人工审批后发布；生产效果监测、自动停止/回滚与责任审计。
- 风险：存在自动晋级机制不等于具备安全自主优化；若缺少完整回归集、发布授权和回滚门禁，可能把偶然高分或评测偏差带入生产。
- 建议：P1/P2 分层推进。P1 将现有 Prompt Experiment/Evaluation/Approval/Registry/Release Gate 接成受控发布闭环；P2 再扩展至 Agent、Workflow、工具配置。生产 Prompt、权限和策略变更必须由明确审批/发布门禁控制，不得无审批自动修改。

### D1-05 企业知识图谱中心

**成熟度：基础已具备。**

- 代码证据：
  - agent_platform/graph/pipeline.py：GraphExtractionPipeline.propose() 校验租户、项目、来源映射、实体/关系 Schema、候选数量与置信度；低置信度进入 Review Required；confirm()、resolve_conflict()、reject() 支持人工确认、冲突解决与拒绝。
  - agent_platform/graph/extraction.py：GraphExtractionService.extract_source()、PostgresGraphCandidateStore 支持抽取候选和 PostgreSQL 持久化。
  - agent_platform/graph/governance.py：GraphGovernanceService 支持纠正、归档、删除、合并、版本检查、租户/项目授权；Repository 维护审计事件、历史和失效任务。
  - agent_platform/api/graph.py：实体搜索、邻居查询、指标、图谱可视化与治理 API；agent_platform/production.py 装配 Graph Query/Governance/Extraction。
  - 前端 /operations/knowledge-graph 对应图谱入口；tests/test_graph_extraction_pipeline.py 覆盖来源血缘、低置信度人工审查、冲突、跨租户拒绝、项目权限与幂等候选 ID；另有 graph governance/query/API/PostgreSQL 测试文件。
- 已实现边界：图谱 Schema、候选抽取、人工审查、来源关联、租户/项目范围、查询和治理的核心机制存在。
- 未获得充分证据的子功能：客户/供应商/产品/物料/合同/订单等企业主数据域的完整 Schema 和连接器映射；跨系统实体消歧与主数据匹配准确率；关系时态/有效期和业务事件关联覆盖；冲突检测的生产质量指标；图谱构建任务在大规模语料上的性能/增量更新；数据血缘与删除传播的全链路运行验收。
- 风险：有通用 Graph Engine 不等于企业知识图谱已具备完整业务语义。默认抽取器/合成测试不能证明真实业务数据准确性。
- 建议：保留现有 Graph 模块；P1 补真实连接器映射、实体消歧、增量更新与删除传播验收；P2 扩展行业 Schema 和知识冲突/时态关系分析。避免另建平行图谱存储。

### D1-06 多模态 AI 中心

**成熟度：部分实现（运行时契约基础）。**

- 代码/文档证据：
  - docs/architecture/runtime-multimodal-capabilities.md 声明 Harness 可按 Adapter 声明 text/image/pdf/audio/video 输入/输出模态；媒体对象包含不透明资源引用、MIME、大小和 SHA-256；不支持的模态/格式必须在调用前拒绝。
  - docs/architecture/multimodal-runtime-media-safety.md 规定禁止调用方直接传 URL/文件路径，需由平台授权资源 ID 解析并校验租户、Run、摘要和媒体策略。
  - tests/test_harness_media_contract.py 存在，测试使用 synthetic references；不能证明真实 Provider 多模态质量、编解码或存储集成。
- 已实现边界：统一运行时媒体能力声明/校验契约与安全边界；不能认定已有图片理解、OCR、语音识别/合成或视频理解模型服务。
- 未获得实现证据的子功能：OCR 与文档版面/表格解析；语音 ASR/TTS、说话人分离；实时音视频流与时间轴理解；视频摘要/片段定位；CAD/工程图纸理解；多模态模型供应商适配清单、路由、降级、成本与质量评测；异步媒体处理、转码、病毒扫描、保留/删除策略及前端上传/预览/标注工作流。
- 风险：媒体格式契约不等于模型具备相应模态能力；如果没有真实 Provider Adapter 与授权媒体解析链路，UI 上传成功也不能证明模型实际理解。
- 建议：P1 先完成一条受控图片/PDF 端到端链路（存储、授权、扫描、Adapter、引用溯源和评测）；P2 再按业务价值增加音频、视频和图纸能力，逐 Provider 建立兼容性矩阵。

### D1-07 AI 事件驱动中心

**成熟度：基础已具备（基础设施层）。**

- 代码证据：
  - agent_platform/events/outbox.py：PostgresOutboxStore.claim_batch()/mark_published()/mark_failed() 与 OutboxRelay.publish_pending() 支持数据库 Outbox 领取和发布。
  - agent_platform/events/consumer.py：RedisStreamsConsumer 使用 receipt store、consumer group、pending reclaim、acknowledge 和 DLQ；IdempotentEventHandler 为业务 Handler 提供幂等效果执行协议。
  - agent_platform/events/webhooks.py、webhook_worker.py、webhook_replay.py 提供 Webhook 订阅、投递、重试/重放基础。
  - 前端路由 /admin/scheduler/jobs、/admin/scheduler/subscriptions、/admin/webhooks 提供调度/事件订阅/Webhook 管理入口；相关测试包括 tests/test_event_consumer.py、tests/test_idempotent_event_effects.py、tests/test_webhook_dead_letter_replay.py。
- 已实现边界：事件可靠投递基础设施、订阅与 Webhook 投递机制存在。之前专项审查确认 Outbox + Redis Streams 有 lease/receipt/pending reclaim/退避/DLQ/checkpoint resume 设计，但通用消费者无法强制所有外部业务副作用天然幂等。
- 未获得充分证据的子功能：统一事件目录/Schema 注册与兼容策略；业务事件建模和事件所有者；无代码事件规则编辑器；跨服务条件过滤/聚合/窗口规则；定时、数据变化、告警和外部消息的统一触发器；事件回放权限与影响预估；事件拓扑、积压、重试/DLQ 运营看板；业务副作用的统一幂等键、对账与补偿契约。
- 风险：至少一次投递不能被表述为端到端 exactly-once；外部副作用成功而 receipt 未完成时仍可能重放，必须由业务 Handler/外部系统幂等键或对账机制保护。
- 建议：P1 统一 Event Envelope/Schema Registry、事件目录、去重与业务效果契约；P2 补事件规则中心和可视化运营。保留 Outbox/Streams/Webhook，不再造第二套消息总线。

### D1-08 企业 AI 合同与服务管理中心

**成熟度：尚未找到有效实现证据。**

- 已有可复用基础：agent_platform/api/contract.py、public_contract.py 提供 API 版本、分页、幂等、限流等契约机制；Quota/Budget 模块提供使用额度和预算约束；生产部署和发布配置可作为未来授权验证的运行环境。
- 本轮文件树与已读取 API/模型/迁移/前端路由中，未发现合同、订阅套餐、许可证签发/验证/续期、服务期限或商业结算专属领域模块、数据库迁移或管理页面。通用 API Contract 不是企业服务合同。
- 未获得实现证据的子功能：企业合同档案与条款；服务套餐/模块开通与功能授权；合同生效/到期/续费/停用；用量与套餐额度映射；许可证签发、签名、验证、撤销和轮换；离线授权/宽限期/离线续期；企业版本与功能矩阵；续费提醒、账单/发票与生态收益分配接口。
- 风险：缺少离线授权和合同状态模型会影响私有化/混合部署商业交付；把 Quota 当许可证会混淆“用量限制”和“产品功能/服务期限授权”。
- 建议：P2 单独建设 TenantContract、ServicePlan、Entitlement、LicenseGrant、UsageMeter、RenewalEvent 等模型；与 Quota 分层，授权验证在企业运行平面可离线执行并可审计。

### D1-09 AI 业务连续性管理中心

**成熟度：部分实现。**

- 代码证据：
  - agent_platform/deployment/backups.py 定义 BackupPolicy、BackupCatalog、BackupCoordinator、BackupScheduler、RestoreDrill，并提供备份链、RPO 判断和 RTO 演练状态。
  - scripts/disaster_recovery.py 的 recovery_plan() 明确标注仅规划、不实际停止服务/恢复数据/提升副本/切换 DNS 或流量；validate_record() 校验组件检查、一致性门禁、failover/failback、独立复核、证据引用和实测 RPO/RTO。
  - deploy/disaster-recovery-policy.json、docs/disaster-recovery-runbook.md、docs/disaster-recovery-drill-template.md 和 tests/test_disaster_recovery.py 提供策略/操作手册/演练模板/验证测试。
- 已实现边界：备份计划/目录/协调、恢复顺序与演练证据验证框架存在；本轮未连接真实基础设施，未执行恢复或故障切换。
- 未获得充分证据的子功能：模型 Provider/第三方 API 自动熔断与跨供应商故障切换；运行中长任务的跨进程/跨节点断点恢复；消息积压自动治理和业务补偿；PostgreSQL/Redis/对象存储/密钥/配置的原子一致性恢复点；自动 failover/failback 编排；异地副本与多故障域部署；真实 RPO/RTO 测量和定期全平台演练；副作用账本对账证明恢复后不重复执行已完成动作。
- 风险：备份文件存在不等于平台可恢复；DR CLI 的记录校验不执行实际恢复，不能据此声称 HA/自动容灾已完成。
- 建议：P1 将备份/恢复、Outbox、Run checkpoint、Tool Effect Reconciliation 统一纳入恢复演练；P2 扩展自动故障切换、异地部署和供应商降级。真机验证按本轮要求跳过。

### D1-10 企业 AI 创新实验室

**成熟度：待验证（未发现专属产品闭环）。**

- 已有可复用基础：
  - frontend/src/routes/route-config.ts 有 /admin/workflows 工作流设计、/operations/rag-playground RAG 实验台、/operations/evaluation/* 评测入口、/admin/prompts Prompt 管理入口。
  - agent_platform/observability/prompt_experiments.py 提供 Prompt A/B 实验；agent_platform/observability/evaluation_*.py 提供评测机制；agent_platform/sandbox/oci.py 提供受限执行基础。
- 本轮未发现创新实验室专属路由、项目/实验空间领域模型或“创新项目转生产”服务。
- 未获得实现证据的子功能：业务人员自然语言生成 Agent/Workflow；Prompt/Skill/Tool 辅助创建与版本依赖检查；项目立项、负责人、预算、目标和里程碑；合成/脱敏测试数据集；隔离沙箱中运行与权限模拟；自动评测与人工验收；资产安全扫描、签名与审批；从实验环境到测试/生产环境的晋级、灰度、回滚和运营效果跟踪。
- 风险：现有开发/运营工具分散在多个产品中心；缺少项目容器、隔离实验环境和发布门禁时，实验成果不能安全、可追溯地进入生产。
- 建议：P2 以现有 Agent/Workflow/Prompt/Skill/Registry/Evaluation/Sandbox 复用建设实验项目工作区，不新建并行 Runtime；实验资产必须经权限审查、测试和人工批准后晋级。

## 3. D1 研发差距与依赖顺序

| 优先级 | 差距项 | 依赖/前置 | 可验证验收标准 |
|---|---|---|---|
| P1 | 多模态最小可用闭环（D1-06） | 统一媒体资源授权、对象存储、Harness Adapter、Provider 凭据与评测 | 图片/PDF 上传→租户授权→内容扫描→真实 Provider 处理→引用溯源→评测全链路集成测试通过；不支持的模态在调用前拒绝 |
| P1 | 自主优化安全发布闭环（D1-04） | Prompt Registry、Evaluation Dataset/Runner、Approval、Release Gate、审计 | 实验候选必须绑定 tenant/prompt/version/dataset；无评测证据或无审批时禁止生产晋级；回滚与审计可验证 |
| P1 | 事件副作用一致性（D1-07） | Outbox/Streams/Receipt、Tool Effect Reconciliation、业务 Handler | 在“副作用已成功、receipt 未完成”崩溃注入后重放，不产生重复业务效果；无法自动对账时进入人工接管 |
| P1 | 图谱业务源与数据质量（D1-05） | Connector、Knowledge ACL、Graph Schema/Extraction/Governance | 至少一个真实业务域端到端抽取；实体/关系有来源版本、置信度、人工审查、冲突和删除传播证据 |
| P1 | 业务连续性完整恢复（D1-09） | Backup/DR、Postgres/Redis/Object Storage、Run checkpoint、Outbox、Effect ledger | 在隔离测试环境执行全组件恢复与 failover/failback；记录实测 RPO/RTO；验证已完成副作用不重放 |
| P2 | 决策中心（D1-01） | Metric/KPI、数据血缘、Evaluation、Graph、Approval、Trace | 指标异常可追溯到数据源；至少两种决策方案可比较；审批后执行并记录实际结果 |
| P2 | 数字员工组织模型（D1-02） | Tenant/Organization/RBAC、Agent Registry、Task/Run、Approval | 数字员工有独立身份/岗位/负责人/权限包；岗位变更立即影响授权；任务、绩效、成本与审计可追溯 |
| P2 | 流程挖掘（D1-03） | 企业连接器、统一业务事件 Schema、Case/Activity 语义、数据治理 | 导入真实事件日志后发现流程变体、偏差与瓶颈；优化前后周期/成本指标可复现 |
| P2 | 合同与服务管理（D1-08） | Tenant、Quota、License signing、离线验证、计量 | 合同/套餐授权能控制模块和额度；断开公共控制面仍可验证离线许可；过期/撤销/续期有审计 |
| P2 | 创新实验室（D1-10） | Asset Registry、Prompt/Workflow、Sandbox、Evaluation、Approval/Release | 业务用户创建实验项目，沙箱验证并评测，审批后晋级生产；版本、权限、回滚和责任链完整 |

## 4. 验证范围与限制

- 本轮读取了 main 指定 SHA 的代码文件、前端路由、相关测试和架构契约；对十项能力逐项核验并记录可复用基础及缺口。
- GitHub Tree 文件路径检索未发现专属模块的项目（决策中心、数字员工、流程挖掘、合同服务、创新实验室）；这属于“本轮未找到实现证据”，不将其扩展为对所有动态/间接实现的绝对否定。
- 测试文件存在仅作为测试覆盖线索。本轮未在本地执行 pytest、前端构建或真实 Provider/数据库/Redis/对象存储测试；当前执行环境不具备仓库 checkout 和所需集成服务。
- 真机验证、真实服务器、外部 SaaS 和外部资源演练按调度要求跳过。
- 本次为文档审查交付，不修改产品业务代码。GitHub PR CI 结果以本次提交触发的实际 Actions 状态为准；不能将未启动/运行中的 CI 记为通过。

## 5. D1 完成判定

D1 十项能力均已给出成熟度判断、已找到的代码/页面/测试证据、明确缺口、风险和建议优先级。该结论是战略扩展能力的专项代码差距审查，不代表 Issue #848 全部验收完成，也不建议关闭 Issue。现有 Graph、Events、Evaluation、Prompt、Runtime、Backup 等模块应优先复用，避免重复建设。
