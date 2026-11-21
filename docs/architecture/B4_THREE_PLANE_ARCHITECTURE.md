# B4 WP-C：控制平面、企业运行平面与生态平面目标架构

> **文档性质：目标架构设计，不是现状实现声明。** 本文依据 Issue #848 的混合部署、企业数据驻留、统一治理、受控自主执行和开放共建战略，定义三平面的职责边界、组件、数据流、断网行为和技术栈。文中列出的代码路径仅用于标识当前仓库已有的相关实现入口；路径存在不等于端到端验收或生产就绪。

## 1. 总体架构与信任边界

平台采用逻辑三平面、物理可混合部署的架构：

- **控制平面（统一治理中心）**：管理身份/组织/租户、AI 资产、模型模板、策略审批、配额、版本和配置发布；不承载企业业务主数据，不直接代替运行平面执行用户任务。
- **企业运行平面（执行引擎 + Runtime）**：位于企业信任域，承载 API、任务、工作流、多智能体协作、Agent Runtime、知识/记忆、工具网关、业务状态和审计事实。
- **生态平面（多智能体协作生态 + OpenAPI）**：连接企业内部开发者、外部伙伴、资产目录、SDK/API、MCP/SaaS 和行业方案；外部输入默认不可信，必须经审核、签名、租户授权和运行时隔离。

逻辑平面不等于必须部署成三个独立集群。控制平面可采用 SaaS、私有化或混合形态；企业运行平面优先部署在客户 VPC、私有云或本地环境；生态平面可由平台公共服务承载，也可由企业内部生态门户承载。



### 1.1 平面边界矩阵

| 平面 | 核心职责 | 不允许承担的职责 | 信任边界 |
|---|---|---|---|
| 控制平面 | 企业/租户治理、身份策略、资产目录、模型模板、审批规则、配额、发布与版本管理 | 不保存企业知识原文、业务会话和客户主数据；不直接执行外部工具副作用 | 平台治理域；跨租户访问必须有明确授权与审计 |
| 企业运行平面 | 用户/API 接入、任务调度、工作流、多 Agent 执行、Runtime、知识记忆、业务数据、工具调用与审计 | 不自行放宽控制策略；Agent 不得直连数据库、任意网络或企业业务系统 | 客户数据域；租户隔离、数据驻留和执行授权在此强制落实 |
| 生态平面 | 内外部开发者协作、OpenAPI/SDK、资产分发、伙伴接入、MCP/SaaS/行业连接 | 不持有租户数据库凭据；不因发布者可信而自动获得租户运行权限 | 外部/跨组织不可信域；输入须验证、审批、授权、隔离和可撤销 |

### 1.2 跨平面数据流

1. 管理员在控制平面完成身份、组织、模型模板、策略、配额和资产版本配置；治理变更产生审计记录。
2. 控制平面生成带租户、版本、有效期和签名的配置/策略快照；运行平面主动通过 TLS/mTLS 拉取并验证后激活。
3. 运行平面将版本回执、健康状态和经策略允许的聚合用量摘要回传；默认不上传提示词、会话、知识原文、业务载荷或密钥。
4. 开发者/伙伴通过 OpenAPI、SDK 或资产门户提交扩展；制品经过来源、SBOM、漏洞、许可证、签名和兼容性检查后进入目录。
5. 企业管理员选择固定版本并授权后，控制平面将安装清单和权限策略下发到企业运行平面；运行面在隔离环境加载制品。
6. Agent 的外部系统访问统一进入 Gateway；调用前执行租户/资源授权、风险检查、审批、幂等和审计。

## 2. 控制平面：统一治理中心

### 2.1 目标架构图

~~~mermaid
flowchart TB
  ADMIN["企业管理员 / 平台运营 / 安全管理员"] --> CONSOLE["治理控制台"]
  CONSOLE --> API["管理 API（FastAPI / OpenAPI）"]
  API --> AUTH["身份认证 / 租户上下文 / SSO"]
  AUTH --> IAM["组织 / 成员 / 角色 / RBAC / 资源 ABAC"]
  API --> REG["AI 资产注册中心"]
  REG --> VERSION["Agent / Workflow / Skill / Tool / Connector 版本"]
  API --> ROUTE["模型目录 / 模板 / Provider 路由"]
  API --> PDP["策略决策中心（PDP）"]
  PDP --> APPROVAL["审批策略 / 审批记录"]
  PDP --> QUOTA["预算 / 并发 / 速率 / 成本配额"]
  IAM --> AUDIT["追加式治理审计"]
  REG --> AUDIT
  PDP --> AUDIT
  APPROVAL --> AUDIT
  QUOTA --> AUDIT
  VERSION --> COMPILER["配置编译 / 签名 / 版本锁定"]
  ROUTE --> COMPILER
  PDP --> COMPILER
  QUOTA --> COMPILER
  COMPILER --> SNAPSHOT["签名策略与配置快照"]
  SNAPSHOT -->|"运行面主动拉取；TLS/mTLS"| AGENT["企业侧 Control Agent"]
  AGENT -->|"版本回执 / 健康 / 最小化用量摘要"| RECEIPT["回执与漂移检测"]
  RECEIPT --> AUDIT
~~~

### 2.2 核心组件、边界与数据流

- **身份与组织治理**：企业、租户、部门/团队、用户、服务身份、角色与资源范围；所有授权由服务端可信身份上下文作出。
- **AI 资产注册**：Agent、Workflow、Skill、Tool、Connector、Prompt、模型配置等资产的登记、版本、依赖、发布、停用和回滚；已发布版本不可原地覆盖。
- **模型治理**：模型目录、模型模板、Provider 路由、超时/重试、数据分级、区域约束和成本预算；密钥只存 Secret 引用，不存明文。
- **策略与审批**：统一策略决策（PDP）及运行时策略执行点（PEP）；对数据访问、工具调用和副作用按风险等级实施授权。
- **配额与计量**：租户/部门/项目/用户/Agent 的 Token、并发、速率、费用和资源预算。
- **发布与配置分发**：将批准的策略、模型模板、资产版本编译为有签名、有有效期、可回滚的配置快照；运行面回执用于漂移检测。
- **审计**：治理操作、授权变更、资产发布、策略变更、配置分发和回执均可关联操作者、租户、版本和时间。

**控制面禁止事项**：不能以公共控制面在线作为企业日常运行的前提；不能默认汇聚企业业务原文；不能在没有企业授权的情况下远程执行客户任务；不能让模型输出或 Agent 参数决定自身权限。

### 2.3 关键技术栈

| 能力 | 目标技术栈 | 选型边界 |
|---|---|---|
| 管理 API | Python / FastAPI / OpenAPI | 沿用仓库 Python 服务体系，管理写入走服务端 API |
| 身份 | OIDC / OAuth 2.1、企业 SSO、服务间 mTLS | 人类用户与工作负载身份分离 |
| 授权 | RBAC + 资源级 ABAC；PDP/PEP | 租户、资源、动作、风险均由服务端判断 |
| 元数据 | PostgreSQL | 资产元数据、版本、策略、审批索引和审计 |
| 密钥 | Vault / KMS / Secret Manager | 只传引用或短期凭据，禁止明文入库/日志 |
| 制品 | OCI Registry / 对象存储 + SHA-256 | 不可变制品、摘要校验、签名验证 |
| 分发 | HTTPS 主动拉取 + TLS/mTLS | 私有运行面无需暴露入站管理端口 |
| 观测 | OpenTelemetry / Prometheus | 跨域遥测按字段白名单和数据驻留策略控制 |

## 3. 企业运行平面：执行引擎 + Runtime

### 3.1 目标架构图

~~~mermaid
flowchart TB
  USER["员工 / 企业系统 / 自动化触发器"] --> INGRESS["企业 Ingress / API Gateway / SSO"]
  INGRESS --> AUTHZ["租户上下文 / RBAC-ABAC / 限流"]
  AUTHZ --> API["Task / Session / Run / OpenAPI"]
  API --> SCHED["任务调度 / 优先级 / 并发 / 生命周期"]
  SCHED --> GRAPH["Workflow Orchestrator（LangGraph）"]
  GRAPH --> COORD["多智能体协作协调器"]
  subgraph MULTI["Multi-Agent Execution：受控多专家协作"]
    COORD --> PLAN["Planner / 任务分解"]
    PLAN --> DISPATCH["Dispatcher / 并行分派"]
    DISPATCH --> A1["专家 Agent A"]
    DISPATCH --> A2["专家 Agent B"]
    DISPATCH --> AN["专家 Agent N"]
    A1 --> BOARD["Hybrid Blackboard：事实 / 证据 / 主张 / 冲突"]
    A2 --> BOARD
    AN --> BOARD
    BOARD --> REVIEW["Critic / 交叉质疑 / 证据校验"]
    REVIEW --> ARBITER["裁决 / 汇总 / 人工介入"]
    ARBITER --> COORD
  end
  COORD --> HARNESS["统一 Runtime / Harness Adapter"]
  HARNESS --> CTX["Context Builder：身份 / 任务 / 知识 / 记忆 / 预算"]
  CTX <--> PG[("PostgreSQL：业务事实 / Session / Run / Outbox / 审计")]
  CTX <--> VEC[("pgvector：向量检索")]
  HARNESS --> MODEL["Model Gateway / Provider Adapter"]
  HARNESS --> TOOL["Supervised Tool Gateway / MCP"]
  TOOL --> GUARD["Schema / 资源授权 / 风险 / 审批 / 幂等"]
  GUARD -->|"授权且审批满足"| BIZ["企业系统 / 外部 SaaS / 行业 API"]
  GRAPH -->|"事务 Outbox"| PG
  PG -->|"Relay 发布事件"| REDIS[("Redis Streams：唤醒 / 消费组 / DLQ")]
  HARNESS --> TRACE["OpenTelemetry / Prometheus / Langfuse"]
  TOOL --> TRACE
  CFG["控制面签名配置 / 策略快照"] -->|"校验租户、签名、版本、TTL"| AUTHZ
~~~

### 3.2 核心组件与边界

- **入口与身份**：API、SSO、Webhook 和企业系统接入；解析可信主体、租户和资源范围，不信任客户端自报 tenant/role。
- **任务与调度**：Task、Session、Run 生命周期、优先级、并发限制、超时、取消、重试、恢复、幂等和人工接管。
- **工作流引擎**：LangGraph 管理显式步骤、分支、状态持久化和人工中断；不取代跨 Agent 共享事实模型。
- **多智能体协作**：Planner 拆解任务，Dispatcher 并行分派专家 Agent；专家通过 Blackboard 提交结构化观点与证据，Critic 交叉质疑，Arbiter 汇总/裁决；高风险节点转人工审批。
- **统一 Runtime / Harness**：负责单次 Agent 执行、上下文装配、模型调用、工具意图、超时/取消和运行轨迹；执行循环通过适配器替换，不把某一模型供应商或 Harness 写死。
- **Context Builder**：按权限与预算组装用户、组织、任务、会话、知识、记忆、黑板和工具结果上下文；不得无差别拼接所有历史对话。
- **持久化与事件**：PostgreSQL 是业务事实源；Outbox 是待投递事件事实源；Redis Streams 只负责异步传输、唤醒和消费组，不作为业务状态主库。
- **工具网关**：所有副作用统一经过白名单、Schema、服务端资源解析、租户授权、风险级别、一次性审批、幂等和审计；Agent 不直连数据库、Redis、任意网络或业务系统。
- **模型网关**：按模板路由云端/本地模型，执行数据分级、脱敏、区域策略、预算、超时和熔断。
- **观测与审计**：关联 Task/Run/Agent/Tool/Approval/Trace ID；运行 Trace 可按策略外送，核心审计必须保留在企业侧持久化存储。

### 3.3 关键数据流

1. 用户/企业系统请求经过 Ingress、身份验证、租户授权和限流后创建 Task/Session/Run。
2. 调度器将任务交给工作流；协作协调器分解任务并按权限、技能和负载选择专家 Agent。
3. Context Builder 按 ACL 读取知识、记忆、黑板和历史状态；每个 Agent 只获得当前步骤所需上下文。
4. 专家 Agent 并行产出结构化观点、证据和产物，写入 Blackboard；Critic 对冲突与证据进行校验，裁决节点形成结果或请求人工介入。
5. 模型调用经过 Provider Adapter；工具意图进入 Gateway，完成授权、审批和幂等检查后才可产生外部副作用。
6. 业务状态和 Outbox 事件写入 PostgreSQL；Relay 发布到 Redis Streams；消费者以持久化回执去重并恢复失败事件。
7. Run、审批、工具结果、审计和 Trace 通过关联 ID 形成可追踪链路；只回传企业批准的摘要指标。

### 3.4 关键技术栈

| 能力 | 目标技术栈 | 选型边界 |
|---|---|---|
| API / Ingress | FastAPI、OpenAPI、企业 API Gateway / NGINX / Envoy | OIDC、TLS、限流、请求关联 ID |
| 工作流 | LangGraph + PostgreSQL Checkpointer | 有状态流程、暂停恢复、人工介入 |
| 多 Agent 协作 | Planner/Dispatcher + Hybrid Blackboard + Critic/Arbiter | 并行执行、结构化证据、冲突解决；避免仅靠共享聊天记录 |
| Agent Runtime | 可替换 Harness Adapter | 复用成熟执行循环；平台掌握权限、上下文和生命周期 |
| 工具协议 | MCP SDK + Supervised Tool Gateway | MCP 仅是协议，不是授权边界 |
| 主存储 | PostgreSQL | 业务事实、Run、审批、黑板、Outbox、审计 |
| 向量检索 | pgvector | 与租户 ACL、文档版本和删除生命周期绑定 |
| 事件 | PostgreSQL Outbox + Redis Streams | 至少一次投递、回执去重、死信恢复 |
| 模型路由 | OpenAI-compatible Provider Adapter + 本地模型 | 可替换 Provider；密钥由 Secret 注入 |
| 观测 | OpenTelemetry / Prometheus / Langfuse | Trace、指标、成本和告警；外送可选 |
| 执行隔离 | OCI 容器；高风险场景评估 gVisor / Kata | 网络出口、CPU/内存、文件系统和进程边界 |

## 4. 生态平面：多智能体协作生态 + OpenAPI

### 4.1 目标架构图

~~~mermaid
flowchart LR
  subgraph ACTORS["生态参与者"]
    INTERNAL["企业内部开发团队 / 业务专家"]
    PARTNER["ISV / 集成商 / 行业伙伴"]
    COMMUNITY["第三方开发者 / 模型与工具提供方"]
  end
  subgraph DEVX["开发者开放能力"]
    PORTAL["开发者门户 / 文档"]
    OPENAPI["OpenAPI 契约 / API Key / OAuth Scope"]
    SDK["SDK / CLI / 示例 / Webhook"]
    SANDBOX["测试租户 / 兼容性验证环境"]
  end
  subgraph SUPPLY["资产供应链与治理"]
    SOURCE["源码仓库 / 构建流水线"]
    SCAN["SBOM / 漏洞 / 许可证 / 静态策略检查"]
    SIGN["制品摘要 / 签名 / 来源证明"]
    CATALOG["资产目录 / 发布者 / 版本 / 兼容矩阵"]
    REVIEW["审核 / 认证 / 撤销"]
    PACKAGE["Agent / Workflow / Skill / Tool / Connector 包"]
  end
  subgraph TENANT["企业运行域（安装后）"]
    ADMIN["租户管理员授权"]
    POLICY["控制面策略与版本锁定"]
    RUNTIME["企业 Runtime / 沙箱"]
    GATEWAY["Gateway / MCP"]
  end
  INTERNAL --> PORTAL
  PARTNER --> PORTAL
  COMMUNITY --> PORTAL
  PORTAL --> OPENAPI --> SDK --> SANDBOX
  PARTNER --> SOURCE --> SCAN --> SIGN --> PACKAGE
  INTERNAL --> SOURCE
  COMMUNITY --> SOURCE
  PACKAGE --> CATALOG --> REVIEW
  REVIEW -->|"审核通过的不可变版本"| ADMIN
  ADMIN --> POLICY --> RUNTIME --> GATEWAY
  GATEWAY -->|"授权连接"| EXT["外部 MCP / SaaS / ERP / CRM / OA / MES"]
  CATALOG -->|"目录 API / 元数据"| OPENAPI
~~~

### 4.2 核心组件、边界与数据流

- **OpenAPI 开放平台**：发布稳定 API 契约、版本策略、OAuth Scope/API Key 生命周期、速率限制、幂等约定、Webhook、安全错误码和开发者文档。
- **开发者工具链**：SDK、CLI、模板、测试租户和兼容性验证工具；区分开发/测试/生产环境，凭据不可跨环境复用。
- **资产生态**：支持 Agent、Workflow、Skill、Tool、Connector、Prompt、模型适配器和行业方案包；统一 Manifest、版本、依赖、权限声明和兼容矩阵。
- **伙伴治理**：伙伴主体、工作区、成员、认证状态、提交审核、发布者密钥、支持责任和撤销机制；伙伴身份本身不授予客户租户权限。
- **供应链安全**：构建来源、SBOM、漏洞/许可证扫描、制品摘要、签名和来源证明；租户安装时再次校验签名、版本和权限。
- **多智能体协作生态**：伙伴可交付可组合的专家 Agent、协作角色、Workflow 和 Skill；跨组织协作必须通过显式 API/事件契约、租户授权和数据最小化，不共享平台内部黑板或数据库。
- **外部连接器**：MCP/SaaS/ERP/CRM/OA/MES 等只作为受控外部依赖；所有业务副作用由企业运行面 Gateway 授权和审计。

**生态平面禁止事项**：不能默认读取客户知识库、会话或黑板；不能将发布者签名视为租户授权；不能直接下发未审核资产到生产；不能让第三方扩展携带平台数据库凭据或绕过企业 Gateway。

### 4.3 关键技术栈

| 能力 | 目标技术栈 | 选型边界 |
|---|---|---|
| API 契约 | OpenAPI 3.x / OAuth 2.1 / Webhook | 契约版本化、Scope 最小化、向后兼容 |
| SDK / CLI | Python / TypeScript SDK、CLI | 从 OpenAPI 契约生成并做兼容测试 |
| 资产包 | OCI Artifact / 版本化 ZIP + JSON/YAML Manifest | 摘要寻址、依赖锁、不可变版本 |
| 供应链 | GitHub Actions、SPDX/CycloneDX SBOM、漏洞与许可证扫描 | 构建来源可追溯、策略门禁 |
| 签名 | Cosign / Sigstore 或企业 PKI | 发布者密钥可轮换、撤销和离线验证 |
| 目录 | PostgreSQL 元数据 + OCI Registry / 对象存储 | 元数据与制品分离 |
| 执行隔离 | OCI 容器；按威胁模型评估 gVisor / Kata | 网络、资源、文件系统和系统调用限制 |
| 协作协议 | MCP、Webhook、事件契约、OpenAPI | 协议接入不绕过企业授权 |

## 5. 断网依赖矩阵与离线策略

**总原则：已有授权、已安装且本地依赖齐备的能力可以按策略继续；新授权、策略提升和高风险副作用必须 fail-closed；断网不允许把未完成动作自动补执行。**

| 依赖 / 链路 | 正常用途 | 断网时允许 | 断网时禁止 / 恢复要求 |
|---|---|---|---|
| 控制平面 ↔ 企业运行平面 | 策略、资产版本、模型模板和配置同步 | 在签名有效、未过期的本地快照内运行已批准任务 | 禁止新授权、权限提升和新版本安装；快照过期后受控执行 fail-closed；恢复后校验签名、版本和撤销状态 |
| 企业 IdP / OIDC | 登录、令牌与身份验证 | 已签发且未过期的短 TTL 会话可按本地验证策略使用 | 新登录/令牌刷新可能不可用；禁止无限期离线会话或降级为匿名管理员 |
| PostgreSQL | 业务事实、Run、审批、黑板、Outbox 和审计 | 数据库健康时可独立完成本地事务 | PostgreSQL 不可用时停止需要持久化的写操作；禁止以内存或 Redis 冒充成功 |
| Redis Streams | 异步事件传输、唤醒、消费组 | 本地业务事务继续写 Outbox；可暂缓异步唤醒 | 禁止把 Redis 当业务事实源；恢复后 Relay 重放，消费者按持久化回执去重 |
| 云端模型 Provider | 推理、Embedding、Rerank | 可切换到已部署且已授权的本地模型；或将可重试任务标记等待 | 无本地模型时明确失败/排队，不伪造结果；切换模型须符合数据分级和预算策略 |
| 本地模型服务 | 企业内网推理 | 服务健康且模型/许可证已部署时继续 | 本地模型不可用时不得绕过治理调用未知公网模型 |
| 外部 MCP / SaaS / 企业系统 | 外部工具和业务副作用 | 本地只读与不依赖外部系统的任务继续 | 外部调用失败需记录；L2/L3 不得离线排队后自动补执行；恢复后重验资源状态和审批 |
| 生态目录 / OCI Registry | 搜索、安装、升级资产 | 已安装、签名验证通过且缓存完整的固定版本按本地策略运行 | 禁止新安装/升级/接受新发布者；恢复后同步撤销、安全公告和兼容状态 |
| KMS / Secret Manager | 密钥注入、签名和凭据轮换 | 在明确短 TTL 内使用已注入、仍有效的凭据 | 无法刷新/轮换时到期即停止依赖该凭据的操作；禁止明文落盘或写日志 |
| 审计与观测出口 | Trace、指标、成本和告警 | 外部遥测不可用时可本地缓冲非关键 Trace；核心审计写企业侧持久化存储 | 审计持久化不可用时高风险动作停止；非关键遥测恢复后按策略补传或丢弃 |
| DNS / NTP / 证书状态 | 服务发现、证书与 TTL 判断 | 使用缓存且仍有效的解析/证书材料 | 时钟偏差或证书状态无法确认时，审批、密钥和高风险动作 fail-closed |

### 5.1 离线允许

- 企业侧 PostgreSQL、知识库、向量检索和本地模型均健康时，执行已授权的本地工作流。
- 在未过期、签名有效的策略快照和授权范围内执行 L0 只读、L1 内部可逆操作。
- 使用已经安装、验证过签名和摘要的 Agent/Skill/Workflow 版本。
- 将 Outbox 事件和非关键遥测保存在本地，联网恢复后按幂等规则同步。
- 控制平面不可达时继续不依赖新策略、新授权或外部系统的企业本地业务。

### 5.2 离线禁止

- 策略快照过期、签名错误或撤销状态无法满足要求时继续受控执行。
- 离线提升租户/用户/Agent 权限、提高配额、修改审批规则或信任新的发布者。
- 离线安装未验证扩展、拉取不可信制品或自动升级生产资产。
- 审批服务不可达时执行 L2 外部/不可逆动作；L3 资金、安全和权限动作仍须有权人员直接执行。
- 将 Redis、进程内存、临时文件或模型上下文当作业务事实源。
- 网络恢复后自动补执行断网期间未完成的 L2/L3 动作；必须重新检查参数、目标资源、授权和审批。

## 6. 混合部署拓扑与技术栈汇总

| 部署形态 | 控制平面 | 企业运行平面 | 生态平面 | 适用场景 |
|---|---|---|---|---|
| SaaS 控制 + 私有运行 | 平台托管治理服务 | 客户 VPC/私有云/本地 | 平台公共目录与伙伴服务 | 常规企业混合部署 |
| 私有控制 + 私有运行 | 客户环境 | 客户环境 | 可选择受控同步或内部目录 | 强监管、数据主权 |
| 全私有化 | 客户环境 | 客户环境 | 内部伙伴门户/镜像仓库 | 不能依赖公共 SaaS 的企业 |
| 完全离线 | 预置离线治理包与授权材料 | 本地完整运行栈 | 离线镜像仓库与签名制品包 | 隔离网/高安全环境 |

| 技术域 | 控制平面 | 企业运行平面 | 生态平面 |
|---|---|---|---|
| API / 协议 | FastAPI、OpenAPI、OIDC | FastAPI、企业 Ingress、MCP Gateway | OpenAPI、OAuth、SDK、Webhook、MCP |
| 状态存储 | PostgreSQL 元数据/审计索引 | PostgreSQL 业务事实 + pgvector | PostgreSQL 目录元数据 + OCI 制品仓库 |
| 事件与缓存 | 可选 Redis/NATS 控制事件 | PostgreSQL Outbox + Redis Streams | OCI/CDN 缓存 |
| 身份与安全 | OIDC、RBAC/ABAC、mTLS、KMS | OIDC、资源授权、审批、Secret 注入 | 发布者身份、签名、SBOM、许可证扫描 |
| 执行 | 配置编译、策略决策、版本发布 | LangGraph、Harness Adapter、多 Agent、MCP Gateway | CI Runner、资产验证、隔离沙箱 |
| 可观测 | OpenTelemetry、Prometheus | OpenTelemetry、Prometheus、Langfuse | 构建证明、漏洞与兼容性报告 |

## 7. 当前仓库实现映射与未验证边界

下表用于把目标组件映射到现有代码入口，**不是生产能力验收结论**。本项不修改业务代码，也不把“存在模块”判定为“生产可用”。

| 目标能力 | 当前代码入口（示例） | 本架构中的定位 | 尚需验证/补齐 |
|---|---|---|---|
| 生产 API 组装 | `agent_platform/production.py`、`agent_platform/production_config.py` | 企业运行面 Composition Root 与配置 | 多实例部署、密钥轮换、真实环境故障恢复 |
| 身份与租户 | `agent_platform/identity/service.py`、`agent_platform/identity/` | 控制面策略输入与运行面服务端授权 | 企业 IdP/SSO、全数据面隔离、Worker 再授权 |
| 资产注册 | `agent_platform/control_plane/registry.py` | 控制面 AI 资产元数据与版本 | 全资产统一 Manifest、依赖锁、跨环境晋级 |
| 运行时执行 | `agent_platform/runtime/production_composition.py`、`agent_platform/runtime/` | 企业运行面 Runtime 与 Worker 装配 | Harness 对比、隔离执行、取消/恢复演练 |
| 事件传输 | `agent_platform/events/outbox.py`、`agent_platform/events/redis_streams.py` | PostgreSQL Outbox + Redis Streams | PG/Redis 故障演练、端到端幂等和恢复验收 |
| 扩展信任与隔离 | `agent_platform/extensions/oci_runtime.py`、`agent_platform/sandbox/oci.py` | 生态制品验证与运行面沙箱 | 真实 OCI 运行、网络隔离、全资产签名门禁 |
| 企业部署 | `deploy/production.compose.yaml` | 私有运行面服务、数据库、Redis、对象存储与网络边界 | HA/DR、升级回滚、完全离线部署演练 |
| 多 Agent 协作 | `agent_platform/workflows/`、`agent_platform/blackboard/` | 编排、专家协作和共享事实 | 生产负载、冲突裁决质量、跨 Agent 权限和恢复 |

**已知架构约束**：

1. PostgreSQL 是业务状态与 Outbox 事实源；Redis Streams 只承担事件传输/唤醒。
2. Agent Runtime 通过受控上下文和 Gateway 使用工具，不应持有平台数据库凭据或任意网络出口。
3. 外部扩展默认不可信；签名只证明来源/完整性，不等于租户授权。
4. 控制面不可用不能成为企业本地业务的单点故障；离线授权必须有 TTL、撤销同步和 fail-closed 规则。
5. 多 Agent 协作应通过工作流状态和结构化 Blackboard 形成证据链，不应以共享聊天历史替代任务、证据和裁决模型。

## 8. 架构不变量与验收清单

- [ ] 控制面、企业运行平面和生态平面分别有明确的责任、信任和网络边界。
- [ ] 三张架构图均展示核心组件及平面内/平面间数据流。
- [ ] 每个平面均列出关键技术栈和部署位置。
- [ ] 企业业务原文、知识、会话和密钥默认驻留客户运行域。
- [ ] 控制面策略/配置通过签名、版本、有效期和租户绑定下发。
- [ ] 运行面所有外部副作用经过 Gateway 的授权、审批、幂等和审计。
- [ ] 多 Agent 通过 Planner/Dispatcher、Blackboard、Critic/Arbiter 协作并支持人工接管。
- [ ] OpenAPI/SDK/生态资产分发与企业运行时权限分离。
- [ ] PostgreSQL 为事实源，Redis Streams 不作为业务状态库。
- [ ] 断网矩阵覆盖控制面、身份、数据库、事件、模型、外部工具、生态制品、密钥、审计和时间服务。
- [ ] L2/L3、高风险、策略过期和审批失效时 fail-closed。
- [ ] 文档明确区分目标架构、当前代码入口、可运行证据和生产验收。

## 9. 验证范围与限制

- 本交付为单一 Markdown 架构文档，包含三张 Mermaid flowchart、边界矩阵、数据流、技术栈、离线依赖矩阵、部署拓扑和当前代码映射。
- 已对照 Issue #848 的 Hybrid First、Tenant First、API First、Asset First、Policy First、Runtime First、Security by Design、Open Ecosystem with Isolation 等原则组织设计。
- 本轮不运行真实服务器、数据库、Redis、模型服务、企业 IdP、OCI Registry 或外部 SaaS 联调；不将未执行的集成测试标记为通过。
- 文档中的目标技术选型（如 Kubernetes、Cosign、gVisor/Kata、OAuth 2.1）是架构建议，是否采用需由后续 PoC、威胁模型和生产验收决定。
