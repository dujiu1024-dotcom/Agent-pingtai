# B4 WP-C：三平面目标架构图

> 状态：目标架构，用于指导后续实现与验收；不代表当前代码已全部生产化。部署基线为混合部署：控制面可托管或私有化，企业运行面优先位于客户信任域，生态接入按租户授权。本项不要求真实服务器或外部资源。

## 1. 总体边界与平面关系

~~~mermaid
flowchart TB
  subgraph CP["控制平面 Control Plane：治理与控制"]
    IAM["身份 / 组织 / 租户 / RBAC"]
    REG["Agent / Skill / Tool / Workflow 注册与版本"]
    MODEL["模型目录 / 模板 / 路由策略"]
    POLICY["策略 / 配额 / 审批 / 审计规则"]
    OPS["管理 API / 控制台 / 发布运维"]
  end
  subgraph RP["企业运行平面 Enterprise Runtime Plane：数据与执行"]
    EDGE["企业 API / SSO / Ingress"]
    ORCH["任务与工作流编排"]
    RUN["Agent Runtime / Harness"]
    GW["监督式工具网关 MCP"]
    BB["共享黑板 / 任务状态"]
    KB["知识库 / 检索 / 记忆"]
    DATA[("PostgreSQL + pgvector")]
    BUS[("Redis Streams + Outbox")]
    OBS["Trace / Metrics / Audit"]
    MP["模型访问代理 / Provider"]
  end
  subgraph EP["生态平面 Ecosystem Plane：扩展与互联"]
    PARTNER["合作伙伴 / 行业方案"]
    CATALOG["扩展目录 / 签名 / 来源"]
    EXT["Skills / Agent / Workflow 包"]
    MCP["外部 MCP / SaaS API"]
    VENDOR["模型供应商"]
  end
  CP -->|"签名配置、策略、版本、授权"| RP
  RP -->|"状态、用量、审计摘要、健康信号"| CP
  RP <-->|"受控调用 / 事件 / 数据交换"| EP
  PARTNER --> CATALOG --> EXT --> REG
  MCP --> GW
  VENDOR --> MP
  EDGE --> ORCH --> RUN
  RUN --> BB --> DATA
  RUN --> KB --> DATA
  RUN --> GW --> OBS
  ORCH --> BUS
  RUN --> MP
~~~

| 平面 | 负责什么 | 不负责什么 | 信任边界 |
|---|---|---|---|
| 控制平面 | 身份/租户治理、注册版本、策略、审批规则、配额、发布与配置分发 | 不承载客户业务主数据；不直接执行 Agent 工具副作用 | 平台管理域；跨客户操作必须按租户授权并审计 |
| 企业运行平面 | API 接入、任务/工作流、Agent 执行、黑板、知识/记忆、工具调用、业务数据与观测 | 不自行决定全局策略；Agent 不得绕过网关直连业务系统 | 客户数据域；租户隔离与数据驻留在此强制执行 |
| 生态平面 | 合作伙伴扩展、行业 Agent/Skill、MCP 服务、模型与 SaaS 提供方 | 不持有平台内部权限；不直接写租户数据库 | 默认不可信；扩展需来源校验、签名/版本审查、权限声明和沙箱 |

逻辑边界不因部署拓扑合并。私有化场景允许控制面与运行面均落在客户环境；生态平面始终按外部不可信依赖处理。

## 2. 控制平面目标架构

~~~mermaid
flowchart TB
  UI["管理控制台 / 管理 API"] --> AUTH["身份认证与租户上下文"]
  AUTH --> IAM["组织 / 成员 / 角色 / RBAC / ABAC"]
  UI --> REG["Agent / Skill / Tool / Workflow 注册中心"]
  REG --> VER["版本 / 制品元数据 / 发布通道"]
  UI --> CFG["模型模板 / Provider 配置 / Secret 引用"]
  UI --> POL["策略中心：风险、工具权限、数据策略"]
  UI --> APR["审批策略与记录索引"]
  UI --> QUOTA["配额 / 预算 / 速率 / 成本策略"]
  IAM --> AUDIT["管理操作审计"]
  REG --> AUDIT
  POL --> AUDIT
  APR --> AUDIT
  QUOTA --> AUDIT
  IAM --> SYNC["签名配置编译、分发、漂移检测"]
  VER --> SYNC
  CFG --> SYNC
  POL --> SYNC
  QUOTA --> SYNC
  SYNC -->|"mTLS 拉取 / 签名快照"| AGENT["客户侧运行平面控制代理"]
  AGENT -->|"版本回执 / 健康 / 用量摘要"| SYNC
~~~

**边界与职责**
- 管理 API 是控制面唯一写入口；控制台不直接写数据库。
- 身份、租户、角色、资源范围由认证中间件从可信会话解析，不接受 Agent 自报身份。
- 注册中心管理声明式元数据与不可变版本；运行面只加载批准、签名有效且策略允许的版本。
- 模型模板只保存路由参数和 Secret 引用；密钥由部署环境 KMS/Secret Manager 注入。
- 策略快照需版本化、签名并带有效期；快照过期或签名无效时不得放宽权限。
- 配置分发回传版本回执、健康、成本汇总和审计索引；业务载荷默认留在客户侧。

**数据流**
1. 管理员经 SSO 进入控制台，控制面解析租户与角色，所有变更写入审计。
2. 扩展和工作流经过静态校验、来源/签名检查与审批后生成不可变版本。
3. 管理员配置模型模板、配额和风险策略，控制面生成签名配置快照。
4. 运行面经 mTLS 主动拉取或受控推送；校验签名、租户、版本和有效期后激活。
5. 运行面回传版本回执、健康与成本摘要；业务内容默认不跨出客户数据域。

**建议技术栈**

| 能力 | 技术 | 说明 |
|---|---|---|
| 管理 API | FastAPI / OpenAPI | 与现有 Python 模块一致，接口契约先行 |
| 身份 | OIDC / OAuth 2.1、企业 SSO、服务间 mTLS | 用户身份与服务身份分离 |
| 授权 | RBAC + 资源级 ABAC | 租户、项目、资源范围由服务端校验 |
| 元数据 | PostgreSQL | 事务、约束、版本索引 |
| 密钥 | KMS / Vault / 云 Secret Manager | 数据库只存引用，不存明文 |
| 制品 | OCI Registry / 对象存储 + SHA-256 | 不可变制品、摘要与签名验证 |
| 配置分发 | HTTPS 拉取 + mTLS；可选 NATS | 主动拉取适合私网部署 |
| 指标 | OpenTelemetry / Prometheus | 仅回传策略允许的遥测字段 |

## 3. 企业运行平面目标架构

~~~mermaid
flowchart TB
  CLIENT["企业用户 / 企业系统"] --> INGRESS["API Gateway / Ingress / SSO"]
  INGRESS --> AUTHZ["租户上下文 / RBAC-ABAC / 限流"]
  AUTHZ --> TASK["Task / Session / Run API"]
  TASK --> WF["LangGraph Workflow Orchestrator"]
  WF --> HARNESS["Agent Runtime / Harness Adapter"]
  HARNESS --> CTX["Context Builder：Memory / KB / Blackboard"]
  CTX --> PG[("PostgreSQL：业务事实 / Run / Outbox")]
  CTX --> VEC[("pgvector：向量索引")]
  HARNESS --> MODEL["Model Gateway / Provider Adapter"]
  HARNESS --> TOOL["Supervised Tool Gateway / MCP"]
  TOOL --> APPROVE["审批：动作摘要 / 资源 / 操作者 / TTL"]
  APPROVE -->|"L0/L1 按策略；L2 有效审批；L3 人工执行"| BIZ["企业内部系统 / SaaS"]
  WF --> REDIS[("Redis Streams：唤醒与消费组")]
  HARNESS --> TRACE["OpenTelemetry / Langfuse / 审计"]
  TOOL --> TRACE
  CTRL["控制面签名配置代理"] -->|"策略快照 / 注册表 / 模型模板"| AUTHZ
  CTRL --> WF
~~~

**边界与职责**
- 运行面是客户数据与执行信任域；业务事实、会话、Run、黑板和知识原文默认驻留客户环境。
- Runtime 只接收最小化上下文，不直接访问数据库、Redis、任意网络或企业业务系统。
- 所有工具副作用统一经过工具网关：白名单、Schema、资源解析、租户授权、风险分级、审批、幂等和审计。
- LangGraph 管理显式流程状态、分支、暂停/恢复；Blackboard 管理跨 Agent 事实、证据、主张、冲突和裁决。
- PostgreSQL 是业务事实源；Outbox 是待投递事件事实源；Redis Streams 只负责唤醒与传输。
- Knowledge Base、Memory、Conversation、Run、Trace 保持不同生命周期，不混成无限增长的聊天记录。
- 模型请求经 Provider Adapter，出站前执行数据分级、脱敏、区域路由和供应商策略。

**数据流**
1. 用户请求经 Ingress、SSO、租户授权和限流后创建 Task/Session/Run。
2. 编排器按版本化 Workflow 生成步骤；Runtime 通过 Context Builder 读取有 ACL 的知识、记忆和黑板。
3. 模型调用经模型网关完成路由、超时、成本计量与数据策略检查。
4. Agent 提交工具意图；网关验证 Schema、资源授权和风险等级，必要时等待绑定动作摘要的一次性审批。
5. 工具结果、审计和 Run 状态写入 PostgreSQL；Outbox 发布事件，Redis 消费端幂等处理。
6. Trace 记录运行链路、Token、延迟、失败与审批关联 ID；遥测字段遵循客户数据策略。

**建议技术栈**

| 能力 | 技术 | 说明 |
|---|---|---|
| API / Ingress | FastAPI + 企业 API Gateway / NGINX / Envoy | OIDC、TLS、限流、请求 ID |
| 工作流 | LangGraph + PostgreSQL Checkpointer | 有状态流程、人工中断和恢复 |
| Agent 执行 | 可替换 Harness Adapter | 不自研执行循环，工具必须受控注入 |
| 工具协议 | MCP SDK + Supervised Tool Gateway | MCP 是协议层，授权/审批由平台执行 |
| 主数据 | PostgreSQL | 事实源、事务、Outbox、审计索引 |
| 向量检索 | pgvector | 与租户 ACL 和文档版本绑定 |
| 事件 | PostgreSQL Outbox + Redis Streams | 至少一次投递、回执幂等、死信恢复 |
| 模型路由 | OpenAI-compatible Provider Adapter | 模型可替换，密钥由 Secret 注入 |
| 观测 | OpenTelemetry + Prometheus + Langfuse | Trace、指标、成本和告警 |
| 部署 | Docker / Kubernetes | 按规模选择私有化部署形态 |

## 4. 生态平面目标架构

~~~mermaid
flowchart LR
  PARTNER["行业 ISV / 集成商 / 开发者"] --> SRC["源码仓库 / OCI Registry"]
  SRC --> CI["构建与安全扫描：SBOM / 依赖 / 漏洞"]
  CI --> SIGN["制品签名 / 来源证明 / 版本摘要"]
  SIGN --> CATALOG["生态目录：发布者 / 权限 / 兼容矩阵"]
  CATALOG --> REVIEW["平台审核 / 许可证检查 / 风险评级"]
  REVIEW --> INSTALL["租户管理员安装与授权"]
  INSTALL --> CONTROL["控制面注册与策略审批"]
  CONTROL -->|"签名、授权、配额、版本锁定"| SANDBOX["运行面沙箱 / 隔离执行"]
  SANDBOX --> GATEWAY["Gateway / MCP 受控连接"]
  GATEWAY --> SERVICE["外部 MCP / SaaS / 行业 API"]
  SANDBOX --> TELEMETRY["最小化遥测 / 兼容性反馈"]
  TELEMETRY --> CATALOG
~~~

**边界与职责**
- 生态目录保存发布者身份、制品摘要、签名、许可证、兼容性、权限声明和审核状态，不托管租户业务数据。
- 合作伙伴提交的 Agent/Skill/Workflow/Tool 包均视为不可信输入；发布者身份不等于运行权限。
- 制品需经过 SBOM、依赖/漏洞扫描、许可证检查、静态策略检查、签名与来源证明；高风险扩展需人工审核。
- 租户管理员显式安装并授予最小权限；授权按租户、环境、资源范围和版本绑定，可撤销。
- 扩展必须在运行面隔离边界执行；外部系统访问经 MCP/工具网关，禁止携带平台数据库凭据。
- 遥测默认只回传聚合健康、版本和错误指标；不得默认上传提示词、业务内容、知识原文或客户标识。

**数据流**
1. 合作伙伴构建扩展包并提交源码或 OCI 制品。
2. CI 生成 SBOM，执行依赖/漏洞扫描和许可证检查，对制品摘要签名。
3. 目录审核发布者、权限声明、兼容矩阵和审核结果。
4. 租户管理员选择固定版本，控制面执行策略审批并下发签名安装清单。
5. 运行面验证签名和摘要，在沙箱加载扩展；外部调用只可使用授权 Gateway 工具。
6. 运行面按数据策略回传最小化遥测，供兼容性与安全治理使用。

**建议技术栈**

| 能力 | 技术 | 说明 |
|---|---|---|
| 扩展制品 | OCI Artifact / 容器镜像 / 版本化 ZIP | 制品不可变，摘要寻址 |
| 元数据 | JSON/YAML 声明式 Manifest | 权限、依赖、入口、兼容版本明确 |
| 供应链 | GitHub Actions、SPDX/CycloneDX SBOM、漏洞扫描 | 构建可追溯 |
| 签名 | Cosign / Sigstore | 验证制品身份与完整性 |
| 沙箱 | 容器；高风险场景 gVisor / Kata Containers | 网络、CPU、内存、文件系统隔离 |
| 工具互联 | MCP + Gateway | 生态服务不直连租户存储 |
| 许可证 | SPDX 标识符 + 自动检查 + 人工复核 | 未知/不兼容许可证不得进入生产 |
| 目录 | PostgreSQL 元数据 + OCI Registry | 目录索引与制品存储分离 |

## 5. 断网与依赖降级矩阵

默认策略：“已有授权的本地能力可继续；新授权和高风险副作用 fail-closed；事件和状态本地持久化并可恢复”。

| 依赖/链路 | 正常用途 | 断网行为 | 安全边界与恢复 |
|---|---|---|---|
| 控制面 ↔ 运行面 | 配置、策略、版本、回执 | 使用未过期签名快照继续已授权任务；禁止新策略、扩展和权限提升 | 快照过期后拒绝受控新执行；恢复后校验版本与漂移 |
| 企业 IdP / OIDC | 登录与令牌验证 | 已建立会话按短 TTL 和本地签名策略续用；新登录可能不可用 | 禁止无限期离线登录；紧急管理员操作双人审批并审计 |
| PostgreSQL | 业务事实、Run、黑板、Outbox | 不可用时停止需要持久化的写操作；不得以内存/Redis 冒充成功 | 恢复后从事务状态恢复，写操作使用幂等键 |
| Redis Streams | 事件唤醒、消费组 | 暂停异步唤醒；Outbox 保留待发布事件 | 恢复后 Relay 重放，消费回执去重 |
| 模型供应商 | 推理与 Embedding | 可切本地模型；否则明确失败或排队，不伪造成功 | Provider 熔断、超时和成本策略；恢复后按任务策略重试 |
| 外部 MCP / SaaS | 外部工具连接 | 外部调用失败并记录；本地只读能力可继续 | L2/L3 不得离线排队后自动补执行，恢复后重验审批和资源 |
| 生态目录 / OCI Registry | 查目录、拉取扩展 | 已安装且签名验证通过的扩展按本地策略继续；新安装/升级停止 | 缓存制品摘要校验；恢复后同步撤销与安全公告 |
| KMS / Secret Manager | 密钥与签名材料 | 仅在短 TTL 缓存窗口使用已注入密钥；不能获取或刷新新密钥 | 禁止 Secret 写日志/配置；缓存过期 fail-closed |
| Langfuse / 遥测出口 | 运行追踪与成本观测 | 本地缓冲或丢弃非关键遥测；核心审计写本地持久化表 | 遥测故障不阻断低风险流程；审计存储不可用时高风险动作停止 |
| DNS / 时间同步 | 证书、TTL、服务发现 | 使用本地缓存；时钟偏差超阈值拒绝审批/证书敏感操作 | 恢复后校验时钟与证书状态 |

**离线允许**
- 在未过期签名策略快照下执行已批准版本的本地工作流。
- 访问客户侧本地 PostgreSQL/知识库和本地模型。
- 执行 L0 只读与 L1 内部可逆写入，并记录本地审计。
- 暂存 Outbox 事件和非关键遥测，网络恢复后同步。

**离线禁止**
- 策略快照过期后继续受控执行，或离线提升角色/权限/配额。
- 离线安装未验证扩展、更新模型策略或接受新发布者信任。
- 控制面/审批服务不可达时放行 L2 外部或不可逆动作；L3 必须由有权人员直接执行。
- 将 Redis、进程内存或临时文件提升为业务事实源。
- 断网期间的 L2/L3 动作恢复联网后自动补偿执行；必须重新确认参数、资源状态和审批。

## 6. 技术栈总表与混合部署映射

| 能力 | 控制平面 | 企业运行平面 | 生态平面 |
|---|---|---|---|
| API / 协议 | FastAPI、OpenAPI、OIDC | FastAPI、企业 Ingress、MCP Gateway | OCI Registry API、MCP |
| 状态存储 | PostgreSQL 元数据/审计索引 | PostgreSQL 业务事实 + pgvector | PostgreSQL 目录元数据 |
| 缓存/事件 | 可选 Redis/NATS 控制事件 | Redis Streams + PostgreSQL Outbox | OCI/CDN 缓存 |
| 身份与安全 | OIDC、RBAC/ABAC、mTLS、KMS | OIDC、资源授权、审批、Secret 注入 | Sigstore/Cosign、SBOM、许可证扫描 |
| 执行 | 配置编译、策略评估、发布 | LangGraph、Harness Adapter、MCP SDK | CI Runner、容器/gVisor/Kata 沙箱 |
| 可观测 | OpenTelemetry、Prometheus | OpenTelemetry、Prometheus、Langfuse | 构建证明、漏洞与兼容报告 |

- **SaaS 控制面 + 私有运行面**：控制面管理元数据、策略和部署回执；运行数据默认不出客户域。
- **私有控制面 + 私有运行面**：适合数据主权、强监管或完全离线客户。
- **混合控制面 + 私有运行面**：运行面主动出站拉取签名配置，避免开放入站端口。
- **完全离线**：预置签名配置包、模型和扩展制品；离线授权有明确有效期，通过受控介质更新和审计。

## 7. 架构不变量

1. 控制面配置与扩展制品必须可版本化、可签名、可验证、可回滚。
2. 运行面业务事实唯一写入 PostgreSQL；Redis 只做传输和唤醒。
3. Agent 不持有平台数据库凭据，不可绕过 Gateway 访问外部系统。
4. 租户身份、角色、资源范围和审批上下文来自可信服务端。
5. L2/L3 fail-closed；断网、审批过期、参数变化均不能放行。
6. 外部扩展默认不可信，按最小权限、沙箱、供应链验证和可撤销授权治理。
7. 业务数据驻留客户运行域；跨域只传策略允许的配置、回执和最小化遥测。
8. 观测故障不得吞掉核心审计；审计不可用时高风险动作停止。
9. 工作流、黑板、知识、记忆、对话、Run 和 Trace 保持不同语义与生命周期。
10. 离线能力受签名快照 TTL 和风险等级约束，不允许隐式放宽安全策略。

## 8. 与现有代码基线映射（目标设计，不代表全部生产化）

| 架构能力 | 仓库落点/决策 | 仍需生产化验收 |
|---|---|---|
| 身份与租户 | agent_platform/identity/ | 企业 IdP 端到端接入、密钥轮换和跨域信任 |
| Agent Runtime | agent_platform/runtime/ | Harness PoC、隔离执行、取消/恢复演练 |
| 工作流 | agent_platform/workflows/ | 多实例 Checkpointer、人工审批恢复 |
| 工具安全 | agent_platform/gateway/、agent_platform/tools/ | 生产 MCP endpoint、mTLS、网络策略和外部联调 |
| 黑板 | agent_platform/blackboard/ | 并发规模、保留策略、跨租户安全测试 |
| 存储/事件 | agent_platform/storage/、agent_platform/events/ | HA、备份恢复、Redis 故障演练 |
| 配额/成本 | agent_platform/quotas/ | Provider 成本校准、预算告警 |
| 可观测 | agent_platform/observability/ | Trace 脱敏、指标基数治理、告警 |
| 扩展生态 | agent_platform/extensions/ | OCI/Sigstore 生产链路、沙箱、许可证扫描 |
| 部署迁移 | agent_platform/deployment/、agent_platform/switching/ | 多环境部署、灰度、回滚和断网演练 |

## 9. 验收与文档自测

- [x] 三张 Mermaid 图分别展示控制平面、企业运行平面、生态平面及交互关系。
- [x] 每个平面均定义职责、禁止事项、信任边界、数据流和技术栈。
- [x] 断网矩阵覆盖控制面、身份、PostgreSQL、Redis、模型、MCP/SaaS、生态制品、KMS 和观测。
- [x] 明确离线 L2/L3 fail-closed，Redis 不作为事实源。
- [x] 明确目标架构不等于当前全部实现。
- [x] 不依赖真实服务器、数据库、Redis、模型密钥或外部资源。
