# A4 WP-A：Issue #848 第 2/7/8 节目标功能原子化清单

- Issue：[#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- 基线：`main` @ `b622d5fc9eab1c79c28dcaa13367eec3c138679d`
- 范围：仅拆解 Issue 原文第 2、7、8 节目标和子功能；不实现产品功能。
- 代码证据口径：路径是本轮可定位的代码入口/已有审查证据；路径存在不等于功能完成。未取得逐项运行证据的项目不升级为“已完成”。
- 验证限制：本轮不申请真机、真实服务器、外部伙伴或第三方服务；不宣称本地集成测试已通过。以 PR CI 结果补充文档格式/仓库门禁证据。

## 1. 成熟度口径

| 状态 | 判定标准 |
|---|---|
| 已完成 | 前后端关键链路完整、真实场景可运行、关键测试通过并有验收证据 |
| 基础已完成 | 领域机制/服务/部分 API 或页面存在，但生产接线、覆盖或验收未闭环 |
| 部分实现 | 只有局部模型、服务、API、页面或流程，缺少关键子功能/端到端链路 |
| 仅有设计 | 只找到设计或文档目标，未取得有效代码证据 |
| 尚未建设 | 已检查的代码证据范围没有对应实现；不是对未读文件作绝对不存在断言 |
| 待验证 | 有代码线索但尚无充分证据确认其行为、完整性或可运行性 |

## 2. 第 2 节：目标产品能力全景（原子化）

### 2.1 A. AI 应用与业务层

| ID | 独立检查项 | 代码路径/入口证据 | 成熟度 | 尚缺/判定边界 |
|---|---|---|---|---|
| A-01 | 智能体管理中心 | `frontend/src/routes/route-config.ts`；`agent_platform/control_plane/registry.py`；`agent_platform/api/registry.py` | 基础已完成 | 创建/配置入口存在；调试、测试、发布、版本、停用、回滚需按完整主链逐项验收 |
| A-02 | 多智能体协作中心 | `agent_platform/workflows/multi_expert.py`；`frontend/src` 协作工作台页面 | 基础已完成 | 专家并行、Blackboard、汇总/Review 有实现线索；默认生产 Run 主链接线、失败隔离、动态重分配待验 |
| A-03 | 数字员工管理中心 | `agent_platform/identity/`；`agent_platform/production.py`；`frontend/src/routes/route-config.ts` | 尚未建设（独立资源闭环） | 未证明 DigitalEmployee 作为独立组织身份，不能以 Agent 页面或 Agent 定义替代 |
| A-04 | 工作流自动化中心 | `agent_platform/control_plane/registry.py`；`agent_platform/runtime/`；前端 Workflow 设计路由 | 部分实现 | 有定义/注册线索；节点编排、条件/循环、人工任务、定时、版本、运行与恢复未证明全链闭环 |
| A-05 | 任务执行与会话中心 | `agent_platform/runtime/lifecycle.py`；`agent_platform/api/`；前端 Task/Run/Session 路由 | 基础已完成 | 状态持久化、暂停/恢复、回放有基础；Task-Session-Run-Artifact 完整追溯及副作用幂等待验 |
| A-06 | 行业智能应用中心 | `frontend/src/routes/route-config.ts`；`agent_platform/workflows/` | 待验证 | 未取得行业模板、交付包、配置化部署、行业验收的端到端证据 |
| A-07 | AI 决策应用扩展 | `agent_platform/observability/`；`agent_platform/knowledge/` | 仅有设计/待验证 | 有分析与评测底座线索；经营指标、方案比较、依据追溯、审批执行链未证实 |
| A-08 | 流程挖掘应用扩展 | `agent_platform/events/`；`agent_platform/observability/` | 仅有设计 | 未取得事件日志采集到流程发现/瓶颈分析/优化效果闭环证据 |
| A-09 | 多模态业务应用扩展 | `agent_platform/` 模型/知识接入相关模块 | 待验证 | 不将通用模型适配或文件上传等同于图像、语音、视频、图纸端到端能力 |

### 2.2 B. AI 核心能力与资源层

| ID | 独立检查项 | 代码路径/入口证据 | 成熟度 | 尚缺/判定边界 |
|---|---|---|---|---|
| B-01 | 模型注册与管理 | `agent_platform/control_plane/registry.py`；模型 API/Provider 模块 | 基础已完成 | Provider/Template 有注册线索；密钥、能力声明、生命周期和真实供应商验收待补 |
| B-02 | 模型路由与降级 | `agent_platform/` model routing/provider 模块；`agent_platform/production.py` | 基础已完成 | 候选路由/回退机制存在；真实故障切换、熔断、跨供应商质量与成本基准待验 |
| B-03 | Tool 管理 | `agent_platform/control_plane/registry.py`；`agent_platform/api/` | 部分实现 | Registry/目录存在；统一契约、发布审批、执行身份和生命周期门禁待补 |
| B-04 | MCP 工具接入 | `agent_platform/` MCP Provider/Gateway 模块；`agent_platform/production.py` | 基础已完成 | 接入机制存在；默认执行循环是否强制经 Gateway 仍有审查发现 |
| B-05 | Skill 管理 | `agent_platform/control_plane/skill_registry.py`；`agent_platform/api/` | 基础已完成 | Skill Registry 有代码线索；跨资产依赖、发布签名、安装升级和回滚待补 |
| B-06 | Prompt 管理 | `agent_platform/` Prompt Registry/observability 模块 | 部分实现 | Prompt 注册/版本有线索；生产发布的 Evaluation Gate 接线未闭合 |
| B-07 | 企业知识库 | `agent_platform/knowledge/service.py`；`agent_platform/api/` | 基础已完成 | 摄取、检索、权限基础存在；数据源覆盖、ACL、删除传播和真实语料质量待验 |
| B-08 | 企业记忆 | `agent_platform/memory/service.py`；`agent_platform/memory/review.py` | 基础已完成 | 生命周期/审核/版本线索存在；跨身份、权限、时效与生产提取闭环待验 |
| B-09 | 企业数据接入 | `agent_platform/connectors/`；`agent_platform/production.py` | 部分实现 | Connector 框架/同步有线索；实际适配器清单必须逐厂商核实 |
| B-10 | 企业数据治理 | `agent_platform/data_governance/` | 部分实现 | 生命周期治理模块存在；分类分级、血缘、质量、删除传播和数据面强制执行待补 |
| B-11 | 企业系统集成与连接器 | `agent_platform/connectors/`；Webhook API/Worker | 部分实现 | 框架不代表 ERP/CRM/OA/HR/MES/PLM/办公平台均已适配；需逐一列出真实实现 |
| B-12 | AI 资产统一注册 | `agent_platform/control_plane/registry.py`；`agent_platform/extensions/manifest.py` | 部分实现 | Registry 与 Extension Manifest 等多套局部契约并存，缺跨类型统一资产描述 |
| B-13 | AI 资产版本管理 | `agent_platform/control_plane/registry.py`；`agent_platform/extensions/lifecycle.py` | 基础已完成（局部） | 局部版本/回滚存在；统一依赖锁、兼容矩阵、环境晋级和灰度待补 |
| B-14 | AI 资产依赖治理 | `agent_platform/extensions/manifest.py`；`agent_platform/extensions/manager.py` | 部分实现 | Extension 精确依赖有实现；Agent/Workflow/Skill/Tool/Prompt/Connector 跨资产依赖图未证实 |
| B-15 | AI 资产发布、回滚、导入导出 | `agent_platform/api/registry.py`；`agent_platform/extensions/` | 部分实现 | Registry 发布/回滚和 Extension 生命周期有线索；统一包格式、环境晋级、签名与跨资产导入导出待验 |

### 2.3 C. AI 运行与运营层

| ID | 独立检查项 | 代码路径/入口证据 | 成熟度 | 尚缺/判定边界 |
|---|---|---|---|---|
| C-01 | 统一 Agent Runtime | `agent_platform/runtime/`；`agent_platform/production.py` | 基础已完成 | Runtime 模块存在；默认生产执行器与多专家/受监督工具主链存在接线差距 |
| C-02 | 任务生命周期管理 | `agent_platform/runtime/lifecycle.py` | 基础已完成 | 状态/持久化基础存在；完整状态转换、取消竞态和恢复验收待补 |
| C-03 | 任务调度 | `agent_platform/runtime/` scheduler 模块；`agent_platform/production.py` | 基础已完成 | 调度实现存在；独立 Worker、身份再授权、容量与优先级端到端待验 |
| C-04 | 事件驱动 | `agent_platform/events/outbox.py`；Redis Streams 相关模块 | 基础已完成 | Outbox/Streams 有实现；故障注入、死信运营和外部副作用幂等待验 |
| C-05 | 多 Agent 协作与上下文传递 | `agent_platform/workflows/multi_expert.py`；Blackboard 模块 | 基础已完成 | 共享上下文/结果有实现；跨任务上下文预算、冲突和恢复一致性待验 |
| C-06 | 中断与恢复 | `agent_platform/runtime/lifecycle.py`；intervention/replay 模块 | 基础已完成 | Checkpoint/干预有线索；跨进程恢复、业务副作用对账和人工接管待验 |
| C-07 | 重试、幂等与补偿 | `agent_platform/events/`；`agent_platform/runtime/` | 部分实现 | 至少一次投递/局部幂等存在；通用副作用 exactly-once 不成立，补偿契约未统一 |
| C-08 | 运行监控、日志、Tracing、告警 | `agent_platform/observability/`；`agent_platform/production.py` | 基础已完成 | Trace/Metrics/Alert 有线索；跨异步组件关联和告警真实投递待验 |
| C-09 | 模型、Token、资源和部门成本 | `agent_platform/observability/`；Quota/Usage 模块 | 部分实现 | 用量/预算基础存在；部门成本中心、云账单对账和准确成本来源待补 |
| C-10 | SLA 与可靠性管理 | `agent_platform/observability/`；`deploy/production.compose.yaml` | 部分实现 | 健康检查/告警有基础；SLO、错误预算、容量、升级和事件复盘未闭环 |
| C-11 | AI 质量评测与持续反馈 | `agent_platform/observability/evaluation_*.py` | 基础已完成（引擎） | Dataset/Runner/Comparison 存在；真实业务质量、人工标注和发布 Gate 全链路待补 |
| C-12 | 企业 AI 价值分析 | `agent_platform/observability/`；前端成本质量页面 | 待验证 | 使用量/成本不等于业务价值；节省工时、流程周期、ROI 归因未获证据 |

### 2.4 D. 企业治理与安全层

| ID | 独立检查项 | 代码路径/入口证据 | 成熟度 | 尚缺/判定边界 |
|---|---|---|---|---|
| D-01 | 企业、部门、团队组织模型 | `agent_platform/identity/`；`frontend/src/components/OrganizationAccessPage.tsx` | 部分实现 | Tenant/User/RBAC 有基础；部门/团队层级、继承和成员生命周期待补 |
| D-02 | 人类用户与数字员工身份区分 | `agent_platform/identity/` | 尚未建设（当前证据范围） | 未证明 DigitalEmployee 独立身份、岗位职责和责任主体模型 |
| D-03 | 身份认证 | `agent_platform/identity/`；`agent_platform/api/identity.py` | 基础已完成 | 登录/Principal 机制存在；企业 IdP、SSO、SCIM 集成待验 |
| D-04 | SSO 与企业目录集成 | `agent_platform/identity/` | 待验证 | 未取得 OIDC/SAML/SCIM 全链路证据 |
| D-05 | RBAC 与策略授权 | `agent_platform/identity/resource_scope.py`；`agent_platform/approval/policy.py` | 基础已完成 | 角色/资源范围基础存在；所有执行入口统一服务端强制待验 |
| D-06 | 企业/部门级多租户隔离 | `agent_platform/identity/`；tenant 相关 migrations | 部分实现 | Tenant 过滤有证据；部门/团队、向量/文件/缓存/事件/异步 Worker 全面隔离未证明 |
| D-07 | 统一策略与审批 | `agent_platform/approval/service.py`；`agent_platform/approval/postgres.py` | 基础已完成 | Approval/Policy 存在；生产 Gateway/Verifier 契约与所有副作用强制接入有缺口 |
| D-08 | 工具调用与业务操作权限 | `agent_platform/` Tool Gateway；`agent_platform/security/credentials.py` | 部分实现 | 有 Gateway/凭据/审批基础；默认执行路径绕过风险和持续授权复核待修 |
| D-09 | 安全执行与沙箱 | `agent_platform/sandbox/oci.py`；`agent_platform/extensions/oci_runtime.py` | 基础已完成（机制） | OCI 安全约束存在；生产执行器强制接线、daemon 边界、网络防绕过待验 |
| D-10 | 密钥与凭据管理 | `agent_platform/security/credentials.py`；`agent_platform/extensions/publishers.py` | 基础已完成 | 凭据作用域/发布者密钥有实现；全服务最小权限、轮换与运行时撤销待验 |
| D-11 | 数据分类分级、隐私和合规 | `agent_platform/data_governance/`；`agent_platform/security/audit.py` | 部分实现 | 生命周期/审计有基础；全数据面分类策略与合规证据包未闭环 |
| D-12 | 全链路审计与责任追踪 | `agent_platform/security/audit.py`；`agent_platform/api/audit.py` | 基础已完成（局部） | 审计 API/迁移存在；身份至副作用/产物关联和生产 DB append-only 权限边界待补 |
| D-13 | 人工接管 | `agent_platform/runtime/` intervention 模块；Approval API | 基础已完成 | 干预/审批存在；暂停、恢复、重新分派和责任交接端到端待验 |
| D-14 | 紧急停止与高风险保护 | `agent_platform/approval/`；`agent_platform/runtime/` | 部分实现 | 风险等级/审批基础存在；全执行器 kill switch、传播时限和演练证据待补 |

### 2.5 E. 开放生态层

| ID | 独立检查项 | 代码路径/入口证据 | 成熟度 | 尚缺/判定边界 |
|---|---|---|---|---|
| E-01 | 企业 AI API 开放平台 | `agent_platform/api/` | 部分实现 | FastAPI 路由存在；API 契约覆盖、版本兼容、开发者自助与生产配额待验 |
| E-02 | Agent/Workflow/Skill/Tool/Connector 资产标准 | `agent_platform/control_plane/registry.py`；`agent_platform/extensions/manifest.py` | 部分实现 | 多套局部标准并存；统一 manifest、包格式和元数据待补 |
| E-03 | 应用与组件市场 | `frontend/src/components/crud/RegistryCatalogPage.tsx` | 部分实现 | 内部 Registry 目录存在；搜索推荐、审核上架、安装/升级/撤销市场流程未闭环 |
| E-04 | 开发者门户、SDK 和文档 | SDK 目录；`agent_platform/api/` | 部分实现 | SDK/API 有基础；完整开发者门户、教程、兼容认证和自助发布待验 |
| E-05 | 企业内部资产共享 | `agent_platform/control_plane/registry.py` | 部分实现 | Tenant-scoped Registry 有线索；企业/部门/团队/个人可见性模型需核实 |
| E-06 | 外部伙伴开发、认证和发布 | `agent_platform/extensions/publishers.py`；`agent_platform/api/extension_publishers.py` | 部分实现（信任密钥局部） | Publisher Key 不等于伙伴主体/工作区/认证/提交审核流程 |
| E-07 | 行业解决方案包 | `agent_platform/extensions/`；`agent_platform/workflows/` | 待验证 | 未取得行业交付包、依赖锁、部署和验收证据 |
| E-08 | 渠道、实施商和伙伴管理 | Identity/Organization 模块 | 尚未建设（当前证据范围） | 未见 Partner、Agreement、Workspace、项目交付和服务运营闭环 |
| E-09 | 授权、订阅、许可和收益分配 | Quota/Usage 模块；`agent_platform/extensions/publishers.py` | 尚未建设（商业权益域） | Quota 是用量计量，Publisher Key 是签名信任；均不等于 License/Entitlement/订阅分润 |

### 2.6 F. 企业部署与基础设施层

| ID | 独立检查项 | 代码路径/入口证据 | 成熟度 | 尚缺/判定边界 |
|---|---|---|---|---|
| F-01 | 企业独立运行环境 | `deploy/production.compose.yaml` | 部分实现 | 单节点 Compose 基线存在；企业隔离部署包、环境生命周期与离线自治待验 |
| F-02 | 混合部署连接与安全边界 | `deploy/production.compose.yaml`；egress proxy 配置 | 部分实现 | 代理配置不等于网络强制；控制面断连自治与数据驻留未证明 |
| F-03 | 版本、配置和环境管理 | `deploy/`；`agent_platform/` rollout 模块 | 部分实现 | 有部署/升级状态机线索；多环境漂移、配置密钥分离与晋级回滚待验 |
| F-04 | 许可证与离线授权 | 当前审查范围未定位到 License/Entitlement 模块 | 尚未建设（当前证据范围） | 缺签名授权、离线缓存、续期宽限、撤销同步与防回拨 |
| F-05 | 数据存储 | `deploy/production.compose.yaml`；PostgreSQL repository modules | 基础已完成（单机基线） | PostgreSQL/Redis/对象存储配置存在；HA、容量、权限分离和恢复验证待验 |
| F-06 | 消息系统 | `agent_platform/events/`；Redis Streams 模块 | 基础已完成 | Outbox/Streams 有实现；跨集群、积压与灾备演练待验 |
| F-07 | 对象存储 | `deploy/production.compose.yaml`；object storage 模块 | 部分实现 | MinIO 有部署线索；API/Worker 最小权限与备份一致性待补 |
| F-08 | 备份与恢复 | `deploy/` backup/restore scripts | 部分实现 | 加密备份/恢复脚本有线索；异地复制、跨组件一致性快照和真实恢复演练待验 |
| F-09 | 容量管理、升级和回滚 | `deploy/`；rollout 模块 | 部分实现 | Compose 资源限制/升级脚本存在；容量预测、HA、升级失败恢复未完整验收 |
| F-10 | 公共控制平面与企业运行平面解耦 | `agent_platform/production.py`；`deploy/production.compose.yaml` | 待验证 | 未证明公共侧不可用时企业核心业务持续运行 |

## 3. 第 7 节：统一资源模型、八大引擎和组织生态

### 3.1 八大统一核心引擎

| ID | 引擎职责原子项 | 代码路径/入口证据 | 成熟度 | 缺口 |
|---|---|---|---|---|
| G-01 | 企业/租户/组织/部门/团队/用户/数字员工统一身份 | `agent_platform/identity/` | 基础已完成（身份基础） | DigitalEmployee 与组织层级、职责模型未形成证据 |
| G-02 | 角色、权限、租户/部门隔离与服务端身份上下文 | `agent_platform/identity/resource_scope.py`；`agent_platform/api/identity.py` | 部分实现 | 全数据面/异步执行路径的租户和部门隔离矩阵待验 |
| G-03 | Agent/Workflow/Skill/Tool/Prompt/Model/Knowledge 资产注册 | `agent_platform/control_plane/registry.py`；`agent_platform/extensions/manifest.py` | 部分实现 | 资产类型不统一，部分领域独立管理 |
| G-04 | 资产版本、依赖、发布、回滚和导入导出 | Registry、Extensions 生命周期模块 | 部分实现 | 跨资产依赖锁、签名、环境晋级和灰度缺失 |
| G-05 | Task 创建、分派、优先级、并发、超时、重试、取消、恢复 | `agent_platform/runtime/`；scheduler/batch/interventions | 基础已完成 | 独立 Worker、身份复核、业务副作用补偿待验 |
| G-06 | Event 触发、订阅、路由、投递、去重和重放 | `agent_platform/events/outbox.py`；Redis Streams/receipt/DLQ 模块 | 基础已完成 | 通用消费端无法保证外部副作用恰好一次 |
| G-07 | Policy 风险判断、授权、审批和执行约束 | `agent_platform/approval/policy.py`；`agent_platform/approval/service.py` | 基础已完成 | 所有 Runtime/Connector/Tool 路径强制策略接入待验 |
| G-08 | Runtime 生命周期、上下文、工具执行、协作、隔离和恢复 | `agent_platform/runtime/`；`agent_platform/workflows/multi_expert.py`；`agent_platform/sandbox/oci.py` | 基础已完成 | 默认执行链与统一 Gateway/多 Agent 工作流存在接线缺口 |
| G-09 | 数据接入、索引、检索、记忆、图谱与数据权限 | `agent_platform/knowledge/`；`agent_platform/memory/`；`agent_platform/connectors/` | 基础已完成（核心能力） | 数据血缘、ACL 全链路、删除传播、多模态摄取待验 |
| G-10 | 日志、指标、Tracing、告警、质量、成本、SLA、价值 | `agent_platform/observability/` | 基础已完成（观测基础） | 跨异步 Trace、准确计量、SLA 与业务价值未闭环 |

### 3.2 统一资源逐项核验

| ID | 资源 | 应独立检查的语义/字段关系 | 代码路径/证据入口 | 成熟度 |
|---|---|---|---|---|
| R-01 | Tenant | 企业边界、状态、标识、数据驻留、隔离键 | `agent_platform/identity/tenants.py`；tenant migrations | 基础已完成 |
| R-02 | Organization | 企业组织、层级、部门/团队父子关系、有效期 | `agent_platform/identity/`；OrganizationAccessPage | 部分实现 |
| R-03 | User | 人类身份、认证主体、组织成员关系、生命周期 | `agent_platform/identity/models.py`；`service.py` | 基础已完成 |
| R-04 | DigitalEmployee | 数字员工身份、岗位、职责、主管、组织成员关系 | 未定位到独立领域模型/资源路由 | 尚未建设（当前证据范围） |
| R-05 | Agent | 定义、配置、负责人、版本、发布状态、权限 | Registry API/Service；Agent 管理前端路由 | 基础已完成（资产注册局部） |
| R-06 | Workflow | 定义、版本、节点、触发器、执行策略 | Registry；Workflow 前端设计路由；Runtime | 部分实现 |
| R-07 | Skill | 可复用定义、版本、依赖、权限 | `agent_platform/control_plane/skill_registry.py` | 基础已完成 |
| R-08 | Tool | 契约、参数 Schema、执行身份、权限和副作用 | Registry/Tool Gateway/MCP 模块 | 部分实现 |
| R-09 | Model | Provider、模型版本、能力、路由、凭据引用 | Model Provider/Template/Router 模块 | 基础已完成（局部） |
| R-10 | KnowledgeBase | 数据源、索引、ACL、版本、删除生命周期 | `agent_platform/knowledge/` | 基础已完成（核心检索） |
| R-11 | Task | 业务目标、发起人、分派、状态、优先级、关联资源 | `agent_platform/runtime/`；Task API | 基础已完成 |
| R-12 | Run | 执行实例、状态、Checkpoint、Trace、租约、结果 | `agent_platform/runtime/lifecycle.py`；Postgres lifecycle | 基础已完成 |
| R-13 | Event | 类型、版本、租户作用域、幂等键、投递状态 | `agent_platform/events/` | 基础已完成（至少一次语义） |
| R-14 | Policy | 适用主体/资源/动作、版本、优先级、决策证据 | `agent_platform/approval/policy.py`；policy API | 部分实现 |
| R-15 | Approval | 审批人、动作摘要、策略版本、过期、消费状态 | `agent_platform/approval/service.py`；`postgres.py` | 基础已完成 |
| R-16 | Artifact | 产物类型、版本、存储位置、访问控制、来源 Run | Artifact/Files API 与 object storage 模块 | 待验证 |
| R-17 | 资源间关系 | Task-Run-Event-Approval-Artifact 外键/租户键/生命周期/审计 | runtime、events、approval、artifact migrations | 待验证（未完成全量迁移关系审查） |

### 3.3 人类员工与数字员工统一组织管理

| ID | 数字员工目标子功能 | 代码路径/证据入口 | 成熟度 | 缺口 |
|---|---|---|---|---|
| DE-01 | 数字员工独立身份类型（区别于 User 与 Agent） | `agent_platform/identity/` | 尚未建设（当前证据范围） | 未证明独立实体与身份认证语义 |
| DE-02 | 企业/部门/团队/岗位归属 | Identity/Organization 模块 | 部分实现 | 组织基础存在；数字员工岗位关联未证实 |
| DE-03 | 职责、服务范围和工作目标 | 未定位独立职责模型 | 尚未建设（当前证据范围） | 缺职责与目标生命周期 |
| DE-04 | 对应 Agent/Workflow/Skill/Tool 绑定 | Registry、Workflow、Skill Registry | 部分实现 | 资产可关联基础存在；数字员工绑定模型与版本固定未证实 |
| DE-05 | 知识范围、数据权限和业务系统权限 | Knowledge ACL、ResourceScope、Connector | 部分实现 | 未证明以数字员工主体统一授权 |
| DE-06 | 人类负责人、主管、审批人与监督人 | Identity、Approval | 待验证 | 未取得数字员工监督关系与审批委托模型 |
| DE-07 | 执行等级、风险边界、额度和工作时间 | Approval Policy、Quota/Budget | 部分实现 | 通用策略/配额存在；数字员工级策略包未证实 |
| DE-08 | 状态、任务、成果和异常接管记录 | Runtime/Task/Artifact/Intervention | 部分实现 | 通用 Run/Task 存在；数字员工维度汇总和接管生命周期未证实 |
| DE-09 | 模型/资源成本、质量与绩效指标 | Observability/Evaluation/Quota | 部分实现 | 成本/质量基础存在；数字员工绩效和组织价值归因缺失 |

## 4. 第 8 节：核心技术能力十项逐项拆解

| ID | 技术能力原子检查项 | 代码路径/入口证据 | 成熟度 | 主要差距 |
|---|---|---|---|---|
| T-01 | 多专家 Agent 并行执行 | `agent_platform/workflows/multi_expert.py` | 基础已完成 | 默认生产 Runtime 未完全接入多专家工作流 |
| T-02 | 复杂任务自动拆解、规划和分配 | `workflows/multi_expert.py` | 基础已完成 | 动态规划质量、重分配和生产任务验收待补 |
| T-03 | Agent 共享任务上下文/Blackboard/中间结果 | Blackboard 模块；`tests/test_platform_postgres_integration.py` | 基础已完成 | 并发冲突、权限边界和跨进程恢复待验 |
| T-04 | 多 Agent 结果汇总、质量校验和冲突处理 | `workflows/multi_expert.py` | 基础已完成 | 真实任务质量基准与异常专家隔离待验 |
| T-05 | 人工介入后暂停、等待、恢复和重新分配 | Runtime intervention/approval 模块 | 部分实现 | 暂停恢复存在；重新分配和完整责任链待验 |
| T-06 | 受监督委派、范围/权限/责任/结果验收 | Identity/Approval/Tool Gateway | 部分实现 | 委派主体、授权范围、验收责任统一模型未证实 |
| T-07 | 任务生命周期、状态机、优先级、进度和记录 | `agent_platform/runtime/lifecycle.py` | 基础已完成 | 状态转换全量覆盖与 SLA 计时待验 |
| T-08 | 多轮会话持久化及 Session-Task 关联 | Sessions API/Store；Runtime | 基础已完成 | 跨会话权限和任务关联完整性待验 |
| T-09 | 任务中断、取消、人工干预和断点恢复 | Runtime lifecycle/interventions/replay | 基础已完成 | 竞态、跨进程恢复和外部副作用对账待验 |
| T-10 | 任务产物管理、版本、下载、追溯和交付 | Artifact/Files API；object storage | 部分实现 | 版本、权限、来源 Run 和交付回执需核验 |
| T-11 | 长任务恢复不重复执行业务动作 | Runtime lifecycle；events/idempotency | 部分实现 | 通用副作用 exactly-once 不成立；需幂等键/对账/补偿 |
| T-12 | 外部工具、MCP、Webhook、数据库/API/消息系统接入 | `agent_platform/connectors/`；MCP；Webhook API | 基础已完成（框架） | 各厂商实际适配清单和生产联调待补 |
| T-13 | 连接器配置、授权、凭据引用和状态监控 | Connector Service/Repository；Credentials | 部分实现 | 健康检查、凭据轮换和同步回执端到端待验 |
| T-14 | ERP/CRM/OA/HR/MES/PLM/财务及办公平台适配 | `agent_platform/connectors/` | 待验证 | 必须逐厂商列出适配器和测试，不得由框架推断已支持 |
| T-15 | 统一工具调用网关及连接器 SDK/测试/发布规范 | Tool Gateway、MCP、Connector 模块 | 部分实现 | 默认 Runtime Gateway 强制接线、SDK/兼容认证待补 |
| T-16 | 工具白名单、JSON Schema 参数校验 | Tool Gateway/Tool Definition 模块 | 基础已完成（局部） | 所有生产调用路径一致执行待验证 |
| T-17 | 执行身份绑定、短期 Token、凭据托管和最小权限 | `agent_platform/security/credentials.py`；Service Identity | 基础已完成（局部） | Worker 续跑身份复核、存储服务账号最小权限待补 |
| T-18 | OCI/容器隔离执行环境及生命周期 | `agent_platform/sandbox/oci.py`；Extension OCI Runtime | 基础已完成（机制） | 生产执行器强制使用和真实隔离测试待验 |
| T-19 | 文件/网络/CPU/内存/时间/并发限制 | `agent_platform/sandbox/oci.py`；`deploy/production.compose.yaml` | 基础已完成（配置/机制） | 网络防绕过、资源压力和逃逸负向测试待验 |
| T-20 | 高风险审批、紧急终止、结果核对和审计 | Approval、Runtime Intervention、Audit | 部分实现 | 全副作用强制审批、全局 kill switch 和核对闭环待补 |
| T-21 | 事件总线、异步任务分发、定时/业务事件触发 | `agent_platform/events/outbox.py`；scheduler | 基础已完成 | 事件源覆盖与生产多实例验收待补 |
| T-22 | 消息确认、Pending 恢复、消费幂等和去重 | Redis Streams/receipt 模块 | 基础已完成 | 依赖 handler 契约；外部副作用幂等需专项验证 |
| T-23 | 失败重试、指数退避、死信队列和重放 | Events/Outbox/DLQ 模块 | 基础已完成（局部） | 运营工具、权限审计、积压恢复演练待验 |
| T-24 | 超时、取消、补偿、优先级、并发和容量控制 | Runtime/Scheduler/Quota | 部分实现 | 跨 Worker 的统一容量与补偿策略未闭环 |
| T-25 | Agent/Workflow/知识/任务/会话 API | `agent_platform/api/` | 部分实现 | API 覆盖和契约版本需生成完整清单 |
| T-26 | Webhook 注册、签名、密钥轮换、事件推送和重放 | Webhook API/Store/Worker/DLQ | 基础已完成（机制） | 真实外部回调、密钥轮换和租户隔离待验 |
| T-27 | API Key、调用身份、权限、配额、限流、幂等和审计 | Identity/Credentials/Quota/API modules | 部分实现 | API Key 生命周期与限流在所有路由的一致强制待验 |
| T-28 | OpenAPI、SDK、SSE/流式通信和版本兼容 | FastAPI API；SDK 目录 | 部分实现 | OpenAPI 契约、SDK 覆盖和兼容性自动化待补 |
| T-29 | 多模型供应商接入、路由、降级和备用切换 | Model Provider/Router 模块；`agent_platform/production.py` | 基础已完成 | 真实供应商故障切换与基准测试待验 |
| T-30 | LangGraph/Strands/其他 Harness 统一适配 | Runtime Harness Adapter；`agent_platform/runtime/harness_benchmark.py` | 基础已完成（适配基础） | 跨 Harness 一致性、真实质量/成本 benchmark 待验 |
| T-31 | MCP Client Provider、模型/Harness 能力声明与兼容管理 | MCP Provider、Model Registry | 部分实现 | 能力协商、版本矩阵和降级行为未闭环 |
| T-32 | Harness 适配器测试和跨运行时一致性测试 | `tests/` Harness 相关测试；benchmark 模块 | 待验证 | 本轮未执行测试，需统一测试矩阵 |
| T-33 | 成功率、质量、延迟、Token/成本、失败/恢复基准 | `agent_platform/runtime/harness_benchmark.py`；Observability | 部分实现 | 有基准数据结构；真实模型与业务任务的可重复数据缺失 |
| T-34 | Agent/Workflow/Skill/Tool/Prompt 资产版本 | Registry/Extensions/Prompt Registry | 部分实现 | 统一资产版本语义和不可变依赖快照待补 |
| T-35 | 开发/测试/预发布/生产环境及配置 | `deploy/`；Registry/Rollout 模块 | 部分实现 | 多环境晋级、配置隔离与漂移检测待验 |
| T-36 | 发布校验、依赖兼容、灰度/分批、回滚和记录 | Rollout/Registry/Extensions | 部分实现 | 资产/环境全链路发布门禁和回滚验收待补 |
| T-37 | 扩展组件发布、撤销、升级和版本锁定 | `agent_platform/extensions/` | 基础已完成（Extension 范围） | 不能推断覆盖全部资产类型；全资产签名/撤销门禁待补 |
| T-38 | Trace/Span/模型/工具/多 Agent 步骤关联 | `agent_platform/observability/`；Run Trace | 基础已完成（基础观测） | 跨异步/Worker/外部副作用关联待验 |
| T-39 | 运行记录、Token、耗时、资源成本和失败原因 | Observability/Quota/Usage | 部分实现 | 成本来源精度与失败分类一致性待补 |
| T-40 | Session/Task/Run/Approval/Artifact 追溯 | Runtime/Approval/Artifact/Audit | 部分实现 | 统一关联 ID 和关系约束需全量验证 |
| T-41 | 数据集、评测指标、反馈、回归和版本效果对比 | `agent_platform/observability/evaluation_*.py` | 基础已完成（引擎） | 真实语料、人工标注、发布 Gate 全覆盖待补 |
| T-42 | PostgreSQL/向量库/Redis/消息/对象存储持久化 | `deploy/production.compose.yaml`；Repository/Knowledge modules | 基础已完成（单机基线） | HA、容量、备份一致性和真实恢复待验 |
| T-43 | 健康检查、服务身份、数据迁移、配置和密钥管理 | `deploy/production.compose.yaml`；Identity/Credentials；migrations | 部分实现 | 服务身份最小权限、迁移回滚和密钥轮换演练待验 |
| T-44 | 容器化、私有云/本地/自有云及混合部署 | `deploy/` | 部分实现 | 当前主要是 Compose；Kubernetes/私有云/离线部署流程未证实 |
| T-45 | 备份恢复、灾备、升级回滚、容量和诊断 | `deploy/` backup/restore/upgrade；Health Monitor | 部分实现 | 有脚本/状态基础；异地复制、跨组件恢复演练与 HA 未闭环 |
| T-46 | 企业运行平面与公共控制平面解耦 | `agent_platform/production.py`；`deploy/production.compose.yaml` | 待验证 | 公共控制面断网自治、离线授权和数据不回传需实证 |

## 5. 第 8 节：十四个产品中心逐项拆解

| ID | 产品中心 | 独立功能检查项 | 代码路径/入口证据 | 成熟度 |
|---|---|---|---|---|
| P-01 | 智能体管理中心 | 创建/配置/调试/测试/发布/版本/停用/回滚 | Registry API/Service；Agent 管理路由 | 基础已完成（注册/版本局部）；完整生命周期待验 |
| P-02 | 多智能体协作中心 | 团队/专家配置、任务拆解、协作编排、共享上下文、冲突处理、人工监督 | `workflows/multi_expert.py`；Blackboard；协作前端路由 | 基础已完成；默认生产接线待验 |
| P-03 | 行业智能应用中心 | 行业模板、场景方案、交付包、配置化部署、验收标准 | Workflows/Extensions；行业应用路由线索 | 待验证 |
| P-04 | 工作流自动化中心 | 画布/节点/条件/循环/人工任务/版本/调试/定时触发 | Workflow Registry/Runtime；前端 Workflow 设计路由 | 部分实现 |
| P-05 | 任务执行与会话中心 | Task/Session/Run/执行过程/产物/接管/恢复 | Runtime lifecycle、Sessions、Interventions、Replay | 基础已完成；完整关联和恢复待验 |
| P-06 | 模型与工具中心 | 模型注册/路由/凭据/Tool/MCP 注册/授权/测试/生命周期 | Model Registry/Provider；Tool Gateway/MCP；Credentials | 基础已完成（局部） |
| P-07 | 企业知识与记忆中心 | 数据源/知识库/索引/记忆/权限过滤/引用溯源/更新/质量评估 | Knowledge、Memory、RAG、Graph、Evaluation 模块 | 基础已完成（引擎）；产品闭环待验 |
| P-08 | Agent 应用市场与资产中心 | 目录/搜索/分类/版本/依赖/安装/授权/发布/撤销 | `agent_platform/api/registry.py`；`RegistryCatalogPage.tsx`；Extensions | 部分实现 |
| P-09 | 企业组织与安全中心 | 企业/部门/团队/用户/SSO/RBAC/策略/租户隔离/审批/密钥 | Identity API/Models/ResourceScope；OrganizationAccessPage | 基础已完成（身份基础），部分实现（企业治理） |
| P-10 | AI 治理与合规中心 | 风险分级/策略/数据合规/审计/责任追踪/合规报告 | Approval Policy；Data Governance；Audit API/Page | 部分实现 |
| P-11 | AI 质量评测中心 | 数据集/评测任务/指标/人工标注/回归/版本对比 | `agent_platform/observability/evaluation_*.py`；Evaluation Insights Page | 基础已完成（评测引擎）；标注/发布门禁待补 |
| P-12 | 运行监控与成本中心 | 状态/日志/Trace/告警/Token/模型/资源成本/分析报表 | Observability/Trace/Prometheus/Alerts/Quota；成本质量页面 | 基础已完成（观测）；运营闭环部分实现 |
| P-13 | 企业资源与成本管理中心 | 资源池/模型额度/计算资源/部门预算/配额/成本归集/分摊/预算告警 | Quota/Budget/Usage API 与 Store | 部分实现 |
| P-14 | 企业部署与运维管理中心 | 实例/环境/配置/版本/升级/回滚/备份/恢复/容量/诊断 | `deploy/`；Health/Readiness/Rollout 模块 | 部分实现 |

## 6. 第 8 节：八个新增产品中心逐项拆解

| ID | 新增中心 | 独立功能检查项 | 代码路径/入口证据 | 成熟度 |
|---|---|---|---|---|
| N-01 | 企业系统集成中心 | ERP/CRM/OA/HR/MES/PLM/财务、办公平台、数据库/API/Webhook/消息接入；连接器配置/授权/监控/自定义开发/发布 | `agent_platform/connectors/`；Webhook；MCP | 部分实现（通用框架）；各厂商适配待验证 |
| N-02 | 企业数据接入与治理中心 | 数据源接入/同步/清洗转换/结构化/目录元数据/分类分级/敏感识别/质量/血缘/删除 | `agent_platform/connectors/`；`agent_platform/data_governance/`；Knowledge | 部分实现 |
| N-03 | AI API 开放平台 | Agent/Workflow/知识/任务/会话 API、Webhook、API Key、配额、限流、权限、OpenAPI、SDK | `agent_platform/api/`；Credentials/Quota；SDK | 部分实现 |
| N-04 | AI 资产生命周期管理中心 | Agent/Workflow/Prompt/Skill/Tool/Connector/Knowledge 模板统一管理、版本/依赖/环境/发布/回滚/导入导出/共享/授权 | Registry、Extensions、Prompt/Connector/Knowledge 模块 | 部分实现 |
| N-05 | AI 安全执行与沙箱中心 | 代码/工具隔离、文件/网络隔离、资源限制、高风险确认、凭据托管、异常终止、审计 | `agent_platform/sandbox/oci.py`；Credentials；Approval；Audit | 基础已完成（机制）；生产强制接线待验 |
| N-06 | AI 服务等级与可靠性管理中心 | 可用率/任务成功率/响应时间 SLA/并发容量/超时重试/熔断恢复/降级/备用模型/复盘 | Observability/Health Monitor；Runtime；Model Router | 部分实现 |
| N-07 | AI 人才与开发者生态中心 | SDK/插件连接器 Skill 规范/文档示例/模板/市场/伙伴空间/认证/兼容测试 | SDK；Extensions/Publishers；Registry | 部分实现（开发/发布基础）；伙伴门户与认证未建设 |
| N-08 | 企业 AI 运营与价值分析中心 | 部门使用/自动化任务量/人工工时估算/效率变化/流程周期/成本收益/活跃度/Agent ROI | Observability/Quota/Usage；前端成本质量页面 | 部分实现（成本/用量）；业务价值归因待验证 |

## 7. 第 8 节：十项战略扩展能力逐项拆解

| ID | 战略能力 | 独立功能检查项 | 代码路径/入口证据 | 成熟度 |
|---|---|---|---|---|
| S-01 | 企业 AI 决策中心 | 经营数据/指标监控/异常趋势/风险预测/方案生成比较/依据追溯/审批执行 | Knowledge/Graph/Observability/Approval | 仅有设计/待验证；未见决策闭环 |
| S-02 | AI 数字员工管理中心 | 身份/岗位职责/组织归属/工作范围/权限责任/绩效/任务分配/成果验收/协作/上岗停用调岗 | Identity/Organization；Runtime；Evaluation | 尚未建设（独立数字员工域） |
| S-03 | 企业流程挖掘中心 | 日志采集/流程发现/标准对比/瓶颈分析/人工操作识别/自动化机会/效果分析 | Events/Observability | 仅有设计 |
| S-04 | AI 自主优化中心 | 失败分析/反馈采集/Prompt 配置缺陷/候选建议/测试生成/版本对比/人工审核发布 | Evaluation/Prompt Registry/Rollout | 部分实现（评测反馈基础）；自主优化闭环未证实 |
| S-05 | 企业知识图谱中心 | 实体识别/实体关系/组织客户供应商产品物料项目合同订单关联/关系查询/事件关联/冲突识别 | `agent_platform/knowledge/` graph 模块 | 基础已完成（图谱基础）；企业级本体/治理待验 |
| S-06 | 多模态 AI 中心 | 图片/OCR/版面/语音识别合成/实时音视频/视频分析/图纸工程文件理解/统一调度 | Knowledge ingestion/Model Provider | 待验证；通用文件接入不等于多模态能力 |
| S-07 | AI 事件驱动中心 | 业务事件/定时/数据变化/告警/消息触发/规则/路由/去重/幂等/重试/死信/可观测 | `agent_platform/events/`；Scheduler；Webhook | 基础已完成（事件底座）；全业务事件治理待补 |
| S-08 | 企业 AI 合同与服务管理中心 | 订阅/套餐/功能许可/额度/期限/续费/提醒/企业版本/许可证签发验证续期离线授权 | Quota/Usage；部署模块 | 尚未建设（商业合同/License 域） |
| S-09 | AI 业务连续性管理中心 | 模型/API 故障切换/长任务恢复/消息积压/任务补偿/一致性/灾备演练/人工接管/防重复副作用 | Runtime/Events/Model Router/Deploy Backup | 部分实现；跨组件灾备和副作用演练待补 |
| S-10 | 企业 AI 创新实验室 | 自助 Agent/自然语言工作流/Prompt Skill 辅助创建/测试集/沙箱试运行/项目管理/效果评估/转生产 | Registry/Workflow/Prompt/Evaluation/Sandbox | 部分实现（基础组件分散）；统一实验室工作流未证实 |

## 8. 第 8 节：统一 AI 控制平面八项检查

| ID | 控制平面能力 | 独立检查点 | 代码路径/入口证据 | 成熟度 |
|---|---|---|---|---|
| CP-01 | 统一身份与授权 | 明确执行主体、租户/组织范围、身份类型和授权决策 | `agent_platform/identity/`；ResourceScope；Service Identity | 基础已完成；Worker 再授权及全链路隔离待补 |
| CP-02 | 统一资源注册 | Agent/模型/工具/知识/Workflow/Skill/Connector 统一注册发现 | `agent_platform/control_plane/registry.py`；Skill Registry；Extensions | 部分实现；资产类型契约分裂 |
| CP-03 | 统一任务调度 | 分派/优先级/并发/超时/重试/取消/恢复 | Runtime lifecycle/Scheduler/Batch | 基础已完成；独立 Worker 与执行身份复核待补 |
| CP-04 | 统一策略引擎 | 数据访问/工具调用/业务动作/风险/审批统一决策 | Approval Policy/Service；Tool Gateway | 基础已完成（局部）；默认 Runtime 绕过/Verifier 契约问题待补 |
| CP-05 | 统一事件总线 | 业务/系统/定时事件发布订阅路由与可靠投递 | Events Outbox/Redis Streams/Webhook | 基础已完成；事件目录/Schema 治理和副作用幂等待补 |
| CP-06 | 统一执行运行时 | Agent/Harness 生命周期、上下文、工具执行、状态和隔离 | Runtime/Harness Adapter/MCP/Sandbox | 基础已完成（模块）；生产统一接线未完成 |
| CP-07 | 统一审计追踪 | 身份/策略/审批/模型工具调用/副作用/产物全链关联 | Audit/Run Trace/Approval/Runtime | 部分实现；统一 Trace/副作用对账/审计权限待补 |
| CP-08 | 统一计量结算 | Token/计算资源、企业部门团队 Agent 成本、预算配额、许可套餐和生态结算 | Observability/Quota/Usage | 部分实现（计量/配额）；License/Entitlement/商业结算未建设 |

## 9. 完整性与覆盖统计

| 目标来源 | 原子检查项数量 | 覆盖状态 |
|---|---:|---|
| 第 2 节 A 应用与业务层 | 9 | 已拆解 |
| 第 2 节 B 核心能力与资源层 | 15 | 已拆解 |
| 第 2 节 C 运行与运营层 | 12 | 已拆解 |
| 第 2 节 D 治理与安全层 | 14 | 已拆解 |
| 第 2 节 E 开放生态层 | 9 | 已拆解 |
| 第 2 节 F 部署与基础设施层 | 10 | 已拆解 |
| 第 7 节八大统一引擎 | 10 | 已拆解 |
| 第 7 节统一资源模型 | 17 | 已拆解 |
| 第 7 节数字员工组织管理 | 9 | 已拆解 |
| 第 8 节十项核心技术能力 | 46 | 已拆解 |
| 第 8 节十四个产品中心 | 14 | 已拆解 |
| 第 8 节八个新增产品中心 | 8 | 已拆解 |
| 第 8 节十项战略扩展能力 | 10 | 已拆解 |
| 第 8 节统一控制平面 | 8 | 已拆解 |
| **合计** | **191** | **逐项编号** |

## 10. 成熟度结论与使用限制

1. 本清单把目标逐项拆成可追踪检查项，适合作为后续代码证据矩阵的主键；它不意味着 191 项已完成。
2. “基础已完成/部分实现”表示代码底座或局部链路存在，不代表前端、API、服务、数据库、权限、测试、CI、生产部署已形成闭环。
3. 多项路径指向目录或模块，是定位入口，不应替代具体类/函数/迁移/API 的逐行证据。下一工作包应继续把每一项补成“前端入口→API→Service→Repository/SQL→租户授权→测试→CI”端到端证据。
4. 代码未定位或本轮未检查到的能力使用“待验证/尚未建设（当前证据范围）”，不得将其解释成对整个仓库绝对不存在的断言。
5. 本轮未运行真实 PostgreSQL/Redis/MinIO/模型供应商/企业系统集成测试；未执行真机、真实服务器、外部伙伴或外部资源验证。
6. 本文只完成 A4 WP-A 的目标原子化拆解，不代替 Issue #848 全仓库逐文件审查，也不代表 Issue #848 达到最终验收条件。

## 11. 本工作包交付与验证

- 变更类型：新增审查文档；无产品代码改动。
- 目标文件：`docs/architecture/A4_WP_A_ATOMIC_CAPABILITY_MATRIX.md`
- 静态核对：检查各源章节的覆盖分组、编号唯一性、合计数量和成熟度字段；以 CI 为仓库门禁结果。
- 外部/真机验证：按调度要求跳过。
- 后续建议：A4 WP-B 再补齐 191 项的端到端证据映射；WP-A 本身不扩大为代码修复任务。
