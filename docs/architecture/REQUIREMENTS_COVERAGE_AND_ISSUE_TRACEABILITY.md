# 需求覆盖与 Issue 追踪矩阵（补漏阶段一）

> 状态：阶段性补漏记录，**不是全仓库 100% Issue 审计完成声明**。  
> 关联任务：[#884](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/884)  
> 审查日期：2026-11-21  
> 范围：复核战略/产品总控、数字员工、核心能力、生产化和执行台账的代表性 Issue；读取 #848、#742、#870、#267、#262、#747、#18、#15、#10、#13 的正文及 #848/#267 关键评论；检查当时开放 PR。未逐条读取全部 Issue 正文和全部评论，因此未完成项继续标记为待审计。

## 1. 管理层结论

1. 企业 AI 基础设施、Agent Runtime、多智能体、知识检索、身份安全、开放生态、工作台、部署治理均已有 Issue 入口。
2. 入口存在不等于原子需求完整，也不等于代码实现、自动化测试或生产验收完成。
3. 本轮确认应独立补充的需求域：跨系统业务自动化、企业 AI 价值/ROI、企业知识资产治理、AI 供应链治理、混合部署离线自治。
4. 数字员工全生命周期已有唯一执行入口 #870，应扩充其任职模型和生命周期验收，不再另建重复 Issue。
5. 企业实例、Tenant、Organization、Department、Team、Project、Partner/Marketplace 的边界需要 #848 形成 ADR；目前不能将不同 Issue 的局部描述视为已完成架构裁决。
6. #267 与 #262 的 Closed 状态不能代替逐项验收证据。#267 历史记录明确包含待核验、未合并 PR、CI 失败和现场验收缺失；是否重开应由总控维护者根据当前任务状态决定。
7. 本文档是阶段一缺口登记。完整 Issue 分页、每个 Issue 正文/必要评论、父子关系和关闭依据仍需按 #884 DoD 继续核验。

## 2. 需求原子项追踪矩阵

| ID | 原子需求 | 现有入口/证据 | 覆盖判断 | 处理与验收归属 |
|---|---|---|---|---|
| REQ-CORE-001 | 控制平面、企业运行平面、生态平面及边界 | #848 第 2/7/8 节 | 已定义目标，代码和生产成熟度需独立判定 | 继续 #848 差距审查 |
| REQ-AGENT-001 | 多专家并行、共享上下文、Blackboard 决策和冲突裁决 | #13、#746、#848 | 需求入口完整度较高；真实跨实例/失败接管待补证 | 复用 #13/#746，按真实消息总线验收 |
| REQ-EMP-001 | 数字员工作为独立组织任职实体，而非 Agent 别名 | #848 B5、#870 | 部分；资源域和组织关系仍有缺口 | #870 补模型；#848 审核资源关系 |
| REQ-EMP-002 | 入职、培训、考核、调岗、暂停、离职、权限回收 | #870 | 原有目录/安装/团队能力不能覆盖完整任职生命周期 | 已补充 #870 生命周期状态机和验收 |
| REQ-WF-001 | ERP/CRM/OA/MES 跨系统流程编排、事务幂等、补偿和业务回执 | #8/#9 等连接器/任务能力 | 部分；缺少统一业务流程端到端交付边界 | 新建 #885 |
| REQ-VALUE-001 | Token/费用/延迟/质量计量 | #747 | 已有明确入口；数据源和生产对账仍需证据 | 继续 #747 |
| REQ-VALUE-002 | 人工基线、节省工时、收益归因、部门/岗位 ROI | #747 仅覆盖成本与质量指标 | 缺少完整业务价值核算 | 新建 #886 |
| REQ-KNOW-001 | 知识库解析、索引、RAG、引用、ACL 和删除传播 | #10 | 需求入口较完整，真实样本质量与生产验收待核验 | 继续 #10 |
| REQ-KNOW-002 | 知识资产责任人、血缘、质量、时效、影响分析 | #10、#848 | 部分；资产治理超出 RAG 运行时范围 | 新建 #887 |
| REQ-SEC-001 | 身份授权、审批、审计、沙箱、密钥 | #15 | 已有系统性入口；需逐项核对运行路径证据 | 继续 #15 |
| REQ-SUPPLY-001 | 模型/Agent/Skill/Tool/Connector 来源、签名、SBOM、漏洞和许可证 | #15、#20、#525、#870 | 部分；缺少统一供应链准入、持续监控和撤销闭环 | 新建 #888 |
| REQ-DEPLOY-001 | 部署、升级、备份、恢复、灾备 | #18 | 已有需求入口；真实 RPO/RTO 和演练待验收 | 继续 #18 |
| REQ-DEPLOY-002 | 控制面失联时企业侧离线自治、授权缓存和恢复同步 | #18、#848、#870 | 部分；缺少系统化行为矩阵和现场演练任务 | 新建 #889 |
| REQ-ORG-001 | 企业实例/Tenant/组织/部门/团队/项目/用户的边界和授权继承 | #848、#742、#870、#15 | 架构决策未闭合；#742 明确自托管定位，不能推断为 SaaS 租户模型 | #848 输出 ADR |
| REQ-ECOSYSTEM-001 | 内部/伙伴/公共资产发现、许可、安装、升级、撤销 | #870、#20、#525 | 已有入口；供应链信任与业务价值闭环仍需补充 | #870 + #888 |
| REQ-OPS-001 | 成本、质量、运维、任务、业务价值的统一运营视图 | #742、#747、#848 | 页面入口存在；指标契约和业务闭环部分缺失 | #747、#886 和 #742 页面验收 |
| REQ-GOV-001 | 任务台账状态与代码/PR/CI/现场证据一致 | #267、#262 | 存在状态失真风险；历史评论含未核验项 | 由 #884 输出清理清单，不自动关闭/重开 |

## 3. 本轮新增与补充任务

### 已新建

- [#885 企业业务自动化](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/885)：跨系统流程、幂等、补偿、审批、人工接管和业务回执。P0。
- [#886 企业 AI 价值管理](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/886)：人工基线、节省工时、收益归因、成本扣减和 ROI。P1。
- [#887 企业知识资产治理](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/887)：血缘、责任、质量、时效、权限传播和影响分析。P1。
- [#888 AI 供应链治理](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/888)：来源证明、签名、SBOM、漏洞/许可证门禁、锁版和撤销。P1。
- [#889 混合部署连续运行](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/889)：离线自治、控制面降级、授权缓存、任务恢复和 HA/DR 演练。P1。

### 已补充现有任务

- [#870 数字员工产品化](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/870)：增加 DigitalEmployee 独立任职实体、生命周期状态机、入职/培训/考核/调岗/离职和权限回收验收。
- [#848 战略架构审查](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)：要求输出企业实例/Tenant/Organization/Department/Team/Project/Partner 的 ADR，明确控制面/数据面边界和离线行为矩阵。
- [#884 需求完整性审计](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/884)：登记本轮新增任务、复用边界、状态风险和未完成审计范围。

## 4. 架构决策记录（ADR）必须回答的问题

#848 需要形成明确决策，而不是继续并列描述：

1. 一个自托管实例是否对应一个企业安全域？是否允许单实例托管多个互不信任企业？
2. Tenant 是企业实例内逻辑隔离域还是 SaaS 客户？两种模式是否同时支持？
3. Enterprise/Organization/Department/Team/Project/User/ServiceAccount/DigitalEmployee 的层级、成员关系、资源归属和授权继承如何定义？
4. 企业内部共享、伙伴交付、公共市场资产的可见性、许可、安装授权和运行数据如何隔离？
5. 控制面不可达时，企业侧身份、策略、密钥、任务、审计和已安装资产如何自治？撤销延迟如何治理？
6. 自托管、私有云和混合部署是否共享同一领域模型/API，通过部署 profile 表达差异？

ADR 必须给出候选方案、决策、取舍、反对方案、部署拓扑、资源关系、授权边界、离线行为矩阵和跨边界负向测试。

## 5. 状态与证据风险

### #267 执行台账

#267 当前为 Closed，但其正文和历史评论包含待逐项核验、PR 未合并、CI 失败、现场验收未完成等记录。不能把 Closed 解释为 125 项全部完成。后续应逐项核对最新 main、PR merge 状态、最新 HEAD CI 和现场证据，再由维护者决定是否重开或转入新的活动台账。

### #262 项目总看板

#262 当前为 Closed，但历史定位仍是项目总看板。应确认其是否被新活动总控替代，明确唯一实时状态来源，避免关闭 Issue 被继续当作运行中的项目状态源。

### #742 产品前端总控

#742 明确本期暂不建设独立 Connector 管理 UI。该范围决策应保留；它不代表连接器后端或跨系统业务流程已完成。#885 负责业务流程闭环，不承诺新增独立 Connector 市场/配置页面。

### #848 审查报告

D5 交付记录明确保留 A5 证据矩阵未定位项、C4 未完整阅读路径、C5 测试失败/未覆盖项，以及数字员工资源域、混合部署自治和生产验收缺口。报告交付不等于产品完成；PR #877 的合并状态和最新 CI 需单独核验。

## 6. 不在本阶段宣称完成的事项

- 尚未逐条读取仓库全部 Issues 的正文和必要评论。
- 尚未完成每个 Issue 的父子关系、重复关系、孤儿项和关闭依据清单。
- 尚未将所有历史项目讨论逐条映射到 Requirement ID。
- 尚未对所有已关闭 Issue 的最新 PR/Commit/CI 证据做复核。
- 尚未证明新增需求已实现或生产可用。
- 尚未生成完整的全仓库原子需求矩阵；本表是已核验的代表性原子项和补漏索引。

## 7. 后续执行顺序

1. #848：完成架构 ADR，裁决企业实例、租户、组织和混合部署边界。
2. #885：优先建立跨系统业务自动化闭环。
3. #870：补数字员工独立资源模型和生命周期。
4. #889：定义并验证企业侧离线自治和灾备。
5. #886：建立 AI 业务价值/ROI 计量。
6. #887：建立知识资产治理。
7. #888：建立 AI 供应链可信交付。
8. #884：继续完成全量 Issue 逐项盘点和最终追踪矩阵，不以本阶段文档替代全量 DoD。

## 8. 证据链接

- [#884 需求完整性审计](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/884)
- [#848 战略与架构审查](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- [#742 前端产品总控](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/742)
- [#870 数字员工与开放生态](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/870)
- [#267 执行台账](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/267)
- [#262 项目总看板](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/262)
- [#747 成本与质量中心](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/747)
- [#15 身份授权与安全](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/15)
- [#18 部署升级与灾备](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/18)
- [#10 知识库与 RAG](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/10)
- [#13 多专家 Blackboard 协作](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/13)


## 9. 具体审查缺陷到实施 Issue 的逐项映射（2026-11-21 第二阶段）

本节依据 #848 审查报告 §14、§19–27 与 D4 Backlog，区分“有实施归属”与“已经修复/验收”。任务被 Issue 承接不代表代码已改或 CI 已通过。

| 具体缺陷 | 领域实施 Issue | CI/专项 Issue | D4 任务 | 当前覆盖结论 |
|---|---|---|---|---|
| Workbench retry 混淆 Run ID 与 Task ID，返回 None | [#9](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/9) | [#882](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/882) | P1-03、P0-07B | 有归属；#9 已补独立回归验收 |
| Migration 重复编号 | [#18](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/18) | [#882](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/882) | P0-06A | 有归属；须兼容已部署库和历史 checksum |
| Redis Outbox 重复 claim / Stream 丢失后恢复遗漏 | [#9](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/9)、[#18](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/18) | [#882](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/882) | P0-07B、P1-04、P2-AUD-13 | 有归属；两类故障需分别验收 |
| pgvector 1024 schema 与 1536 默认/测试维度冲突 | [#10](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/10) | [#882](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/882) | P0-07B | 有归属；#10 已补 provider/schema 一致性验收 |
| SQL 参数类型、Prompt Audit action constraint、Memory Service/fixture 契约失败 | 依具体根因归属 #10/#15/#18 | [#882](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/882) | P0-07A、P0-07B | 有归属；逐项区分产品缺陷与测试契约漂移 |
| ModelRuntimeRouter transport/test double 与错误语义漂移 | [#12](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/12) | [#881](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/881)、[#883](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/883) | P0-07A、P0-07C | 有归属；不得缩小静态检查范围或放宽断言 |
| 默认生产工具执行绕过 SupervisedToolGateway、审批消费契约不一致 | [#12](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/12)、[#15](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/15) | [#881](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/881) | P0-02B、P1-AUD-02、P1-AUD-03 | 有明确归属；G0 安全阻断 |
| PostgreSQL/MySQL Connector 未强制只读 | [#15](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/15) | [#882](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/882) | P1-AUD-04 | 有明确归属；真实写权限账号负向测试 |
| Prompt Evaluation Gate 未注入生产发布路径 | #848 审查追踪；Prompt Registry 实施域 | #881/#883（相关 CI 契约） | P1-AUD-06、P1-05 | 有明确归属；发布/激活/回滚均须 fail-closed |
| Run Worker 与 API 生命周期耦合、MinIO readiness 不真实 | [#9](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/9)、[#18](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/18) | — | P1-04、P2-AUD-11 | 有明确归属；多实例与真实对象存储故障待验收 |
| Effect Reconciliation 授权/审计依赖无效，审计 append-only 与 DB 角色冲突 | [#15](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/15) | — | P0-03A/B、P1-AUD-03 | 有明确归属；G0 |
| No-auth 生产误配置、异步执行/重放未重新授权 | [#9](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/9)、[#15](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/15) | — | P0-01A/B/C、P2-AUD-12 | 有明确归属；G0 fail-closed |
| Consumer 外部副作用幂等依赖 Handler 自律 | [#9](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/9) | — | P1-04、P2-AUD-13 | 有明确归属；须覆盖崩溃窗口与 replay |
| 出站代理旁路、MinIO Root 凭据复用 | [#15](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/15)、[#18](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/18) | — | P1-AUD-08/09 | 有明确归属；G0 |
| 异地备份/跨组件一致性、单机 Compose 不构成 HA/混合部署验收 | [#18](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/18)、[#889](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/889) | — | P1-06A/B、P2-AUD-14/16 | 有明确归属；真实演练未完成 |

### 9.1 已有领域 Issue，但原子验收曾不够明确；本轮已补回正文

- [#8](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/8)：文件 API 路由与 OpenAPI/前端/测试契约对齐，覆盖 HTTP 405。
- [#9](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/9)：Workbench retry 的 Task/Run ID 关联、Redis Outbox 重复 claim/恢复遗漏、Worker 执行前重新授权。
- [#10](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/10)：embedding 维度一致性、ACL 列表读取 API、公开/受限 Chunk ACL 语义、Memory Extraction 来源契约。
- [#12](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/12)：默认工具调用 Gateway 强制、ApprovalVerifier Run 绑定契约、ModelRuntimeRouter Protocol/错误语义。
- [#15](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/15)：Worker/异步重放再授权、Effect Reconciliation 审计授权、Connector 只读、生产 No-auth 防护。
- [#18](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/18)：迁移编号兼容治理、Outbox 故障恢复、跨组件备份恢复、对象存储最小权限与 readiness。

### 9.2 不重复建 Issue 的决定

本轮没有为上述具体缺陷再创建重复 Issue。已将缺陷补入既有领域 Issue #8/#9/#10/#12/#15/#18；CI 基线继续由 #881/#882/#883 负责。#881–#883 的 CI 通过不自动代表领域功能验收通过，领域 Issue 仍需自己的代码、集成测试和生产证据。

### 9.3 仍未完成的工作

1. 将上述每项映射同步到 #267 的稳定原子任务编号和证据字段。
2. 更新 D4 Backlog 第 12 节追踪矩阵，逐项列出 §14 的 Workbench retry、迁移编号、Redis Outbox、pgvector 维度和 API/ACL 契约问题；不能只映射到宽泛的 CI 基线任务。
3. 对 #848 报告第 14、19–27 节之外的所有具体发现继续按相同方法逐项映射。
4. #884 的全量 Issue 正文、评论、父子关系、重复项、孤儿项和关闭依据审计仍未完成；本节不是全量审计完成声明。
