# B5 补证：统一资源关系、数字员工组织关系与生态资产生命周期

- 关联总控：Issue [#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- 审查基线：`main@b622d5fc9eab1c79c28dcaa13367eec3c138679d`
- 审查范围：PostgreSQL migrations、Identity/Registry/Runtime/Extension repositories、API、前端组织管理页及对应测试路径
- 文档性质：**现状代码证据 + 目标关系补证**。本文件不新增数据库表、不声称目标模型已实现。
- 状态标签：**已落库**（DDL 存在）；**代码已实现**（Repository/API 可定位）；**部分实现**（仅局部链路）；**目标建议**（尚无物理模型/端到端证据）；**待验证**（缺少本轮可复现运行证据）。

## 1. 结论摘要

1. 当前已经存在 Tenant、User、TenantMembership、Organization、Team、Project、Workspace、Role/Permission/RoleBinding、ServiceAccount、AgentIdentity 等组织与身份基础表；组织成员关系目前以人类 User 为主。
2. Agent 与 DigitalEmployee 不是同一实体。当前 `platform_agent_identities` 将 Agent 绑定到 ServiceAccount，用于机器身份认证；没有找到 DigitalEmployee 专属表、领域模块、API 或独立前端页面，无法证明已有数字员工岗位、主管、责任人、绩效、排班和员工生命周期模型。
3. Agent/Workflow/Tool/Skill/Model Provider/Model Template 共享租户版本 Registry（定义 JSONB）；但 Python Registry 的 `_KINDS` 还包含 `routing_policy`，migration 011 的 kind CHECK 未包含该值，形成代码/DDL 合约不一致。Prompt 使用独立版本表，当前 Prompt 表没有 tenant_id，需作为资产租户边界的高优先级核查项。
4. 生态扩展已有 Manifest、依赖版本锁、发布者公钥信任、租户安装状态、权限回调和安装/启用/升级/禁用/卸载流程；但当前表结构主要覆盖“某租户安装了什么”，不等于已经具备跨租户公共市场、伙伴组织、资产所有权、商业授权、分发范围和收益结算平台。
5. 当前租户隔离较强的证据主要来自复合主键、复合外键、Repository tenant binding 和服务端授权；仍有多态引用或 JSON 引用未被数据库 FK 约束的边界，包括 RoleBinding 的 subject/resource、Session/File/Connector 的 project_id、Knowledge ACL/Collection 对 Document 的引用、Run registry_snapshot 等。
6. 建议保留现有 Identity、Registry、Extension、ResourceScope 与版本化执行快照；补齐 DigitalEmployee 领域、组织/资源授权的引用完整性、资产可见性与发布者/所有权模型，不建议另起一套身份、资产或扩展 Registry。

## 2. 统一资源关系图（当前落库基础 + 目标关系）

~~~mermaid
erDiagram
  TENANT ||--o{ TENANT_MEMBERSHIP : has
  USER ||--o{ TENANT_MEMBERSHIP : joins
  TENANT ||--o{ ORGANIZATION : contains
  ORGANIZATION ||--o{ ORGANIZATION_MEMBERSHIP : has
  USER ||--o{ ORGANIZATION_MEMBERSHIP : joins
  TENANT ||--o{ TEAM : contains
  ORGANIZATION o|--o{ TEAM : groups
  TEAM ||--o{ TEAM_MEMBERSHIP : has
  USER ||--o{ TEAM_MEMBERSHIP : joins
  TENANT ||--o{ PROJECT : owns
  ORGANIZATION o|--o{ PROJECT : sponsors
  PROJECT ||--o{ WORKSPACE : contains
  USER ||--o{ PROJECT_MEMBER : participates
  PROJECT ||--o{ PROJECT_MEMBER : grants
  TENANT ||--o{ ROLE : defines
  ROLE ||--o{ ROLE_PERMISSION : includes
  USER ||--o{ ROLE_BINDING : subject
  TEAM ||--o{ ROLE_BINDING : subject
  SERVICE_ACCOUNT ||--o{ ROLE_BINDING : subject
  TENANT ||--o{ AGENT : owns
  AGENT ||--o{ AGENT_IDENTITY : authenticates_as
  SERVICE_ACCOUNT ||--o{ AGENT_IDENTITY : backs
  TENANT ||--o{ REGISTRY_VERSION : owns
  REGISTRY_VERSION ||--o| REGISTRY_ACTIVE : selected
  TENANT ||--o{ TASK : owns
  TASK ||--o{ RUN : executes
  RUN ||--o{ RUN_EVENT : emits
  RUN ||--o{ APPROVAL : requests
  TENANT ||--o{ FILE_ASSET : owns
  FILE_ASSET ||--o{ FILE_ASSET_VERSION : versions
  TENANT ||--o{ KNOWLEDGE_DOCUMENT : owns
  KNOWLEDGE_DOCUMENT ||--o{ KNOWLEDGE_CHUNK : splits
  KNOWLEDGE_CHUNK ||--o{ KNOWLEDGE_EMBEDDING : indexes
~~~

> 图中 USER/TEAM/SERVICE_ACCOUNT 到 ROLE_BINDING 的边是领域语义；当前数据库 RoleBinding 使用多态 subject_type/subject_id，并未对三类 subject 建立数据库外键。PROJECT 到 Session/File/Connector 的关系也存在服务层 scope 语义，但对应 project_id 列并非全部有 FK。图不是把缺失 FK 画成数据库约束。

### 2.1 统一资源字段与物理映射清单

下表列出当前迁移中的核心字段（省略索引以外的非关键审计列）；复合主键/外键均显式写出。未出现的字段不得被前端或服务层默认推断。

| 资源 | 当前表 / 字段 | 主键 / 唯一约束 | 外键与租户边界 | 当前成熟度 |
|---|---|---|---|---|
| Tenant | `platform_tenants(tenant_id, display_name, status, created_at)` | PK `tenant_id` | 其他租户业务表引用 tenant_id | 已落库 |
| User | `platform_users(user_id, email, email_normalized, display_name, password_hash, status, created_at, updated_at, last_login_at)` | PK `user_id`；email_normalized 全局 UNIQUE | 用户是平台身份主体；加入租户需 TenantMembership | 已落库；全局邮箱唯一是当前实现选择 |
| ExternalIdentity | `platform_external_identities(external_identity_id, user_id, provider, provider_subject, email_claim, created_at)` | PK external_identity_id；UNIQUE(provider, provider_subject) | FK user_id → User | 已落库 |
| TenantMembership | `platform_tenant_memberships(tenant_id, user_id, status, joined_at, created_at)` | PK `(tenant_id,user_id)` | FK tenant_id → Tenant；user_id → User | 已落库 |
| Organization | `platform_organizations(tenant_id, organization_id, name, status, created_at)` | PK `(tenant_id,organization_id)` | FK tenant_id → Tenant | 已落库 |
| OrganizationMembership | `platform_organization_memberships(tenant_id, organization_id, user_id, status, created_at)` | PK `(tenant_id,organization_id,user_id)` | 复合 FK → Organization；复合 FK → TenantMembership | 已落库；仅人类 User |
| Team | `platform_teams(tenant_id, team_id, organization_id, name, status, created_at)` | PK `(tenant_id,team_id)` | 复合 FK → Organization（organization_id 可空） | 已落库 |
| TeamMembership | `platform_team_memberships(tenant_id, team_id, user_id, status, created_at)` | PK `(tenant_id,team_id,user_id)` | 复合 FK → Team；复合 FK → TenantMembership | 已落库；无 DigitalEmployee 成员关系 |
| Project | `platform_projects(tenant_id, project_id, organization_id, slug, name, status, created_at, updated_at)` | PK `(tenant_id,project_id)`；UNIQUE `(tenant_id,slug)` | 复合 FK → Organization | 已落库 |
| Workspace | `platform_workspaces(tenant_id, workspace_id, project_id, slug, name, status, created_at)` | PK `(tenant_id,workspace_id)`；UNIQUE `(tenant_id,project_id,slug)` | 复合 FK → Project | 已落库 |
| ProjectMember | `platform_project_members(tenant_id, project_id, user_id, status, created_at)` | PK `(tenant_id,project_id,user_id)` | 复合 FK → Project；复合 FK → TenantMembership | 已落库；仅 User |
| Role / Permission | `platform_roles(tenant_id,role_id,name,description,is_system,created_at)`; `platform_permissions(permission_key,description,created_at)` | Role PK `(tenant_id,role_id)`、UNIQUE `(tenant_id,name)`；Permission PK permission_key | Role → Tenant；Permission 为全局字典 | 已落库 |
| RolePermission | `platform_role_permissions(tenant_id,role_id,permission_key)` | PK 三列 | 复合 FK → Role；FK → Permission | 已落库 |
| RoleBinding | `platform_role_bindings(binding_id,tenant_id,subject_type,subject_id,resource_type,resource_id,role_id,created_by,created_at,revoked_at)` | PK binding_id；活动绑定部分唯一索引 | FK 仅约束 `(tenant_id,role_id)` → Role；subject/resource 是多态文本引用 | 部分实现；需要 subject/resource 存在性与租户一致性强化 |
| ServiceAccount | `platform_service_accounts(tenant_id,service_account_id,name,status,created_at)` | PK `(tenant_id,service_account_id)` | FK tenant_id → Tenant | 已落库 |
| Agent | `platform_agents(tenant_id,agent_id,display_name,role_definition,skill_refs,status,created_at,updated_at)` | PK `(tenant_id,agent_id)` | FK tenant_id → Tenant | 已落库基础定义；Registry 是版本化主路径 |
| AgentIdentity | `platform_agent_identities(tenant_id,agent_id,service_account_id,status,created_at)` | PK `(tenant_id,agent_id)` | 复合 FK → Agent；复合 FK → ServiceAccount | 已落库；机器身份，不是数字员工 |
| RegistryVersion | `platform_registry_versions(tenant_id,kind,item_id,version,definition,status,created_by,created_at,published_at)` | PK `(tenant_id,kind,item_id,version)` | FK tenant_id → Tenant；created_by 无 User FK | 已落库；JSONB 资产定义 |
| RegistryActive | `platform_registry_active(tenant_id,kind,item_id,active_version,updated_at)` | PK `(tenant_id,kind,item_id)` | 复合 FK → RegistryVersion | 已落库 |
| ModelTemplate | `platform_model_templates(tenant_id,template_id,version,definition,status,created_at,created_by)` | PK `(tenant_id,template_id,version)` | FK tenant_id → Tenant | 已落库；新 Run 可改用 Registry snapshot |
| PromptVersion | `platform_prompt_versions(prompt_id,version,template,variables,content_hash,created_by,created_at)` | PK `(prompt_id,version)` | 当前表无 tenant_id；部署指针亦按 prompt_id 全局唯一 | 已落库但租户归属不足，需审查迁移/兼容边界 |
| Task | `platform_tasks(tenant_id,task_id,actor_id,agent_id,project_id,session_id,input,priority,status,run_id,output,last_error,idempotency_key,attempts,max_attempts,available_at,lease_owner,lease_expires_at,created_at,updated_at,finished_at)` | PK `(tenant_id,task_id)`；租户/actor/idempotency_key 部分唯一 | 复合 FK → Agent、Session、Run；project_id 当前无 FK | 已落库 |
| Run | `platform_runs(tenant_id,run_id,project_id,task_id,agent_id,template_id,template_version,workflow_id,status,input,output,usage,trace_id,started_at,finished_at,created_at,created_by,idempotency_key,request_digest,agent_version,session_id,registry_snapshot,registry_snapshot_digest,...)` | PK `(tenant_id,run_id)`；actor 幂等索引 | 初始 Agent/ModelTemplate 复合 FK 后由迁移 025 移除；Registry snapshot 为 JSONB，非 FK | 部分实现；快照版本引用尚缺数据库参照完整性 |
| RunEvent | `platform_run_events(tenant_id,event_id,run_id,task_id,event_type,actor_id,payload,occurred_at)` | PK `(tenant_id,event_id)` | 复合 FK → Run；复合 FK → Task | 已落库 |
| Approval | `platform_approvals(tenant_id,approval_id,run_id,action,resource,risk_level,action_hash,requested_by,decided_by,status,expires_at,decided_at,consumed_at,policy_version,created_at)` | PK `(tenant_id,approval_id)` | 复合 FK → Run；policy_version 为文本快照 | 已落库；Policy 实体不是独立通用表 |
| FileAsset | `platform_file_assets(tenant_id,asset_id,owner_id,project_id,session_id,object_key,filename,media_type,size_bytes,sha256,status,created_at,current_version)` | PK `(tenant_id,asset_id)`；UNIQUE `(tenant_id,object_key)` | FK tenant_id → Tenant；复合 FK → Session；project_id 无 FK | 已落库 |
| FileAssetVersion | `platform_file_asset_versions(tenant_id,asset_id,version,object_key,filename,media_type,size_bytes,sha256,status,created_at)` | PK `(tenant_id,asset_id,version)`；UNIQUE `(tenant_id,object_key)` | 复合 FK → FileAsset | 已落库 |
| KnowledgeDocument | `knowledge_documents(tenant_id,document_id,version,title,source_uri,content_hash,scope,project_id,owner_user_id,media_type,metadata,created_at,deleted_at)` | PK `(tenant_id,document_id,version)` | FK tenant_id → Tenant；project_id / owner_user_id 无 FK | 已落库；scope 有 tenant/project/private |
| KnowledgeChunk / Embedding | `knowledge_chunks(tenant_id,document_id,document_version,chunk_id,ordinal,body,content_hash,...)`; `knowledge_embeddings(tenant_id,chunk_id,embedding_model,embedding,created_at)` | Chunk PK `(tenant_id,chunk_id)`；Embedding PK `(tenant_id,chunk_id,embedding_model)` | Chunk 复合 FK → DocumentVersion；Embedding 复合 FK → Chunk | 已落库 |
| KnowledgeACL | `knowledge_document_acl(tenant_id,document_id,principal_type,principal_id,permission,granted_by,created_at)` | PK `(tenant_id,document_id,principal_type,principal_id,permission)` | 当前无 FK → Document；principal 为多态文本 | 部分实现；ACL 写入/删除完整性需强化 |
| Event / Outbox | `platform_outbox(tenant_id,outbox_id,event_id,topic,payload,created_at,published_at,attempts,...)`；RunEvent 如上 | Outbox PK `(tenant_id,outbox_id)`；UNIQUE `(tenant_id,event_id)` | Outbox FK tenant；业务实体关联依赖 topic/payload 或 RunEvent | 已落库；不能把任意 JSON payload 当强关系 |
| Policy | 代码策略对象、RoleBinding、Approval.policy_version、QuotaPolicy 等 | 无单一通用 `platform_policies` 主表 | 部分策略具租户范围；通用策略资源版本和主体绑定尚未统一 | 部分实现/目标建议 |
| DigitalEmployee | 未发现专属物理表或领域模块 | 无 | 无 | 目标建议 |
| EcosystemPublisher / CatalogAsset | 发布者信任与租户安装表见第 5 节 | 尚无完整全局目录/伙伴实体主键 | 当前信任键按 tenant + extension + key | 部分实现 |

### 2.2 必须明确的关系约束

- **租户主键贯穿**：租户内资源优先使用复合主键 `(tenant_id, resource_id)`；跨资源引用必须使用同租户复合外键，不能只用 resource_id。
- **组织隶属**：User 必须先有 TenantMembership 才能加入 Organization/Team/Project；组织和团队归属均由 tenant_id + 对象 ID 复合 FK 约束。
- **项目范围**：`project_id IS NULL` 当前表示 Tenant scope，而不是“未知项目”或自动共享给所有项目。非空 project_id 必须额外执行项目成员/资源权限校验。
- **多态主体**：RoleBinding.subject_type/subject_id 和 resource_type/resource_id 不能只靠外键完整校验；必须由服务层解析资源并做 tenant match，必要时增加受控的主体/资源关联表。
- **不可变版本**：Registry/Prompt/FileAsset/Run 的执行引用应固定具体版本，不引用 latest；Run snapshot 当前为 JSONB，需要版本完整性验证与审计。
- **事件关联**：RunEvent 通过复合 FK 关联 Run/Task；Outbox topic/payload 是事件传输载荷，不应被当作实体外键。
- **历史保留**：用户离职、员工停用、资产撤销、版本下线不应级联删除历史 Run、Approval、Audit 和 Artifact；采用状态/墓碑与受控数据删除流程。

## 3. 数字员工组织关系图（目标模型，非当前落库）

~~~mermaid
flowchart TB
  T["Tenant / 企业"] --> O["Organization / 部门"]
  O --> TM["Team / 团队"]
  T --> HUM["Human User / 人类员工"]
  T --> DE["DigitalEmployee / 数字员工身份"]
  HUM -->|"业务负责人 / 直属主管 / 审批人"| DE
  O -->|"组织归属"| DE
  TM -->|"团队成员关系"| DE
  DE --> JOB["岗位 / 职责 / 工作目标"]
  DE --> OWNER["责任人 / 监督人 / 接管人"]
  DE --> AGENT["Agent + 固定 Agent Version"]
  AGENT --> WF["Workflow Version"]
  AGENT --> SKILL["Skill Version"]
  AGENT --> TOOL["Tool Grant / Tool Version"]
  DE --> KB["Knowledge Scope / ACL"]
  DE --> POLICY["Execution Policy / 风险等级 / 额度 / 时段"]
  DE --> TASK["Task Assignment"]
  TASK --> RUN["Run / 执行轨迹"]
  RUN --> APPROVAL["Approval / 人工审批"]
  RUN --> ARTIFACT["Artifact / 交付成果"]
  RUN --> METRIC["质量 / 成本 / SLA / 绩效"]
~~~

### 3.1 数字员工与人类员工的身份边界

| 概念 | 责任主体 | 身份凭据 | 组织成员 | 权限来源 | 审计归因 |
|---|---|---|---|---|---|
| Human User | 自然人 | OIDC/Session/API Credential | Tenant/Organization/Team/Project Membership | 人类用户 RBAC/资源 ACL | user_id |
| DigitalEmployee | 企业配置的数字岗位/服务角色，不是自然人 | 由 AgentIdentity/ServiceAccount 提供机器身份；不得伪装成用户会话 | 应具有独立的部门/团队成员关系 | 员工岗位授权 + Agent 能力授权 + 人类监督策略的交集 | digital_employee_id + agent_id + service_account_id + human approver |
| Agent | 可执行智能体定义/运行时配置 | 通过 AgentIdentity 绑定 ServiceAccount | 本身不是部门员工成员 | Registry 权限、工具 Grant、Runtime Policy | agent_id / run_id |
| ServiceAccount | 工作负载主体 | 短期凭据/令牌哈希 | 不等同于人类组织成员 | 机器身份授权 | service_account_id |

当前 `platform_agent_identities(tenant_id,agent_id,service_account_id,status,created_at)` 解决的是 Agent 的机器身份绑定；不能表达岗位、组织归属、数字员工负责人、绩效或调岗。因此禁止把 Agent display_name 或头像作为 DigitalEmployee 实现证据。

### 3.2 目标 DigitalEmployee 字段与外键建议

建议新增领域实体而非把字段塞入 Agent JSON。以下为目标字段契约，不是当前 DDL：

| 字段 | 类型/约束建议 | 语义 |
|---|---|---|
| tenant_id | text NOT NULL | 企业归属；所有 PK/FK 均包含 tenant_id |
| digital_employee_id | uuid NOT NULL | 稳定数字员工身份 ID |
| employee_code | text NOT NULL | 租户内员工编号；UNIQUE (tenant_id, employee_code) |
| display_name | text NOT NULL | 展示名称 |
| organization_id | text NOT NULL | 部门归属；FK (tenant_id, organization_id) → Organization |
| team_id | text NULL | 团队归属；FK (tenant_id, team_id) → Team |
| position_code / position_name | text | 岗位编码与岗位名称 |
| job_description | text | 职责边界与工作说明 |
| goals | jsonb | 可度量工作目标；版本化或审计变更 |
| owner_user_id | uuid NOT NULL | 业务责任人；FK (tenant_id, owner_user_id) → TenantMembership |
| supervisor_user_id | uuid NULL | 主管/监督人；同租户成员 FK |
| approver_user_id | uuid NULL | 高风险操作审批责任人；同租户成员 FK |
| agent_id | text NOT NULL | 绑定执行 Agent；FK (tenant_id, agent_id) → Agent |
| agent_version | integer NOT NULL | 固定执行版本；复合 FK → RegistryVersion(kind='agent') |
| status | enum | draft / active / paused / suspended / retired |
| execution_level | enum | read_only / draft_only / approval_required / bounded_autonomy |
| policy_ref / policy_version | text | 固定执行策略版本，不允许 Agent 自行提高 |
| schedule | jsonb | 工作时间窗、时区、节假日策略 |
| budget_scope_id / concurrency_limit | text / integer | 资源预算与并发边界 |
| knowledge_scope | jsonb | 可访问知识库/项目/资源的明确引用 |
| created_by / updated_by | uuid | 人类变更责任主体 |
| created_at / updated_at / retired_at | timestamptz | 生命周期时间戳 |

建议配套关联表：

- `platform_digital_employee_memberships(tenant_id,digital_employee_id,organization_id,team_id,member_type,valid_from,valid_to,status)`：支持组织/团队调动历史；所有组织/团队 FK 使用 tenant_id。
- `platform_digital_employee_asset_bindings(tenant_id,digital_employee_id,asset_kind,asset_id,asset_version,purpose,granted_by,created_at,revoked_at)`：Agent/Workflow/Skill/Tool/Model 等固定版本绑定；复合 FK 到 DigitalEmployee 和 RegistryVersion。
- `platform_digital_employee_supervision(tenant_id,digital_employee_id,human_user_id,relationship,valid_from,valid_to)`：负责人、主管、审批人、接管人角色分离；FK 到 TenantMembership。
- `platform_digital_employee_assignments(tenant_id,assignment_id,digital_employee_id,task_id,assigned_by,accepted_at,completed_at,status)`：任务分配需保留数字员工与人类派发者的责任链；FK 到 Task。

### 3.3 数字员工关系完整性规则

1. 数字员工必须隶属且只能隶属一个 Tenant；组织/团队、负责人、审批人和 Agent 均须同租户。
2. 一个 DigitalEmployee 可以绑定一个主 Agent 版本和多个经授权的 Workflow/Skill/Tool 版本；版本升级须显式审批并形成新绑定记录。
3. Team membership 与 Organization membership 不应复用 User-only 表后塞入虚拟 user_id；建议采用明确的 member_type/subject 表或独立 DigitalEmployee membership。
4. 人类负责人、审批人、服务身份是三个不同角色。审批不能因为 Agent 绑定 ServiceAccount 而自动批准。
5. 停用/调岗应撤销新的任务分派和权限，已完成 Run/Approval/Artifact 历史保留并可追责。
6. 绩效只引用可审计的 Task/Run/Artifact/Quality/Cost 证据，不以模型自评作为唯一绩效来源。

## 4. 租户权限与资源访问矩阵

| 资源范围 | 允许主体 | 必须检查 | 禁止行为 |
|---|---|---|---|
| Tenant | Tenant member / Tenant admin / service principal | 主体 tenant_id 与资源 tenant_id 一致；membership active | 仅信任客户端 tenant_id |
| Organization | Tenant member + org-level grant | 组织归属、成员状态、资源动作 | 跨 Tenant 关联 Organization |
| Team | User / DigitalEmployee（目标）/ service identity | team membership、organization scope、role binding | 将 team_id 当成全局 ID |
| Project | User / Agent / DigitalEmployee（目标） | ProjectMember/资源 ACL；tenant_id + project_id | NULL project 自动继承所有项目 |
| Registry Asset | tenant admin/developer/authorized service | registry kind/item/version + tenant scope + action permission | 通过枚举猜测/跨租户读取 |
| Knowledge | User / Agent / DigitalEmployee（目标） | tenant、scope、project/private owner、document ACL 在检索前过滤 | 先召回跨租户内容再在 UI 隐藏 |
| Tool / Connector | Agent runtime / service identity | tool grant、资源解析、参数 schema、风险/审批、secret ref | 把 MCP endpoint 或 Manifest permission 当成授权结果 |
| Task / Run | creator / assigned employee / supervisor / auditor | tenant、actor、assignment、project、run visibility | 仅用 UUID 难猜作为访问控制 |
| Approval | 指定审批人 / 有权代理人 | action_hash、policy_version、资源、风险级别、TTL、一次性消费 | 批准后修改动作参数仍复用批准 |
| Artifact / File | owner / authorized project/team/tenant member | tenant + project/session + ACL + version/status | 仅凭 object_key 或 file path 读取 |
| Ecosystem package | publisher / reviewer / tenant installer | publisher trust、签名、SBOM、权限声明、租户安装授权 | 发布者可信即自动获得客户租户访问 |

**现有授权代码证据**：`ResourceScopeAuthorizer` 要求 principal tenant 与资源 tenant 相等，并将人类 Principal/API Credential 与 ServicePrincipal 分流到对应授权服务；Registry API 从可信 Principal 取 tenant，并核对 Repository tenant。以上是服务层边界，不能替代所有数据表 FK 和资源 ACL。

## 5. 生态资产生命周期图（当前实现与目标市场分层）

~~~mermaid
flowchart TD
  DEV["内部开发者 / 外部伙伴"] --> SUBMIT["提交 Manifest / 源码 / 制品"]
  SUBMIT --> VALIDATE["Manifest Schema / SemVer / 依赖精确锁"]
  VALIDATE --> SCAN["目标：SBOM / 漏洞 / 许可证 / 行为风险 / 兼容性"]
  SCAN --> SIGN["制品 Digest / Publisher Signature / Provenance"]
  SIGN --> TRUST["租户 Publisher Key Trust：register / rotate / revoke"]
  TRUST --> REVIEW["目标：平台/企业审核与发布策略"]
  REVIEW --> CATALOG["目标：公共/伙伴/企业目录；不可变版本"]
  CATALOG --> INSTALL["租户管理员选择版本并授权安装"]
  INSTALL --> PERM["逐项权限授权 + 依赖闭包 + 版本锁定"]
  PERM --> SANDBOX["隔离 Runtime 安装（默认 disabled）"]
  SANDBOX --> ENABLE["启用前审计 / Policy Gate"]
  ENABLE --> RUN["受控执行 / Gateway / Trace / Audit"]
  RUN --> HEALTH["健康、质量、成本、漏洞与兼容监控"]
  HEALTH -->|"新版本审核"| UPGRADE["升级：新版本并行验证/灰度"]
  UPGRADE --> RUN
  HEALTH -->|"漏洞 / 违规 / 许可撤销"| REVOKE["撤销信任 / 禁用 / 停止新执行"]
  REVOKE --> UNINSTALL["卸载与凭据/缓存/索引清理"]
  RUN --> ROLLBACK["回滚至仍受信任的固定版本"]
~~~

### 5.1 当前生态资产实现证据

| 生命周期阶段 | 当前代码/表 | 已实现范围 | 明确缺口 |
|---|---|---|---|
| Registry 定义 | `agent_platform/control_plane/registry.py`、`platform_registry_versions` | Tenant-scoped kind/item/version、JSONB definition、draft/published/disabled/deprecated、active pointer | DB kind CHECK 与 Python kind 列表需对齐；缺少统一 owner/publisher/visibility/asset license 字段 |
| Registry API | `agent_platform/api/registry.py` | list/create version/publish/rollback/diff/get/list versions/disable/active；从可信 Principal 获取 tenant | 需补充组织/团队共享、伙伴目录和市场 API |
| Skill 包 | `agent_platform/control_plane/skill_registry.py` | ZIP/Manifest 校验、摘要、依赖审查、安装边界 | 是否完整执行 SBOM/许可证/漏洞与市场审核需逐流程验证 |
| Extension Manifest | `agent_platform/extensions/manifest.py` | extension_id、SemVer、platform_api、permissions、dependencies、exact dependency_versions、safe entrypoint | 未覆盖统一 Asset Manifest 全字段（owner、license、data/network access、risk score、support lifecycle） |
| Extension lifecycle | `agent_platform/extensions/lifecycle.py`、`manager.py` | 依赖图、循环检测、兼容性、install/enable/upgrade/disable/uninstall、授权与审计回调、补偿 | 缺少完整市场状态机、跨组织发布审批与商业许可授权 |
| Publisher trust | `agent_platform/extensions/publishers.py`、`extensions/postgres.py`、migration 075 | Tenant + extension + key 的公钥登记、轮换、撤销、有效期 | 当前是租户信任配置，不等于全局伙伴组织/发布者档案和声誉体系 |
| Installed extension | `platform_installed_extensions` | Tenant + extension_id 唯一；保存 Manifest JSON、enabled/disabled、immutable artifact_ref | 没有单独 version 列、安装来源、授权协议版本、安装者、安装范围和回滚历史字段（版本目前藏于 Manifest） |
| Connector Manifest | `platform_connector_manifests` | Tenant-scoped connector_id/name/version/manifest/source | 缺少统一发布者、可见性和完整资产生命周期/撤销关系 |
| Connector runtime | `platform_data_connections`、`platform_connector_sync_jobs` | Tenant-scoped secret_ref、scope、status、sync cursor/job | 需进一步核验安装资产与运行连接实例是否有明确关联和授权快照 |
| Prompt | `platform_prompt_versions` / deployments | 版本不可变、active/canary 指针 | 当前表没有 tenant_id，不能直接与租户 Registry 同等看待 |

### 5.2 资产边界模型（必须区分）

| 资产边界 | 所有权 | 可发现范围 | 安装/使用授权 | 当前实现判断 |
|---|---|---|---|---|
| 企业私有 | 单一 Tenant | 仅所属 Tenant 授权主体 | Tenant 管理员/资源 owner 授权 | Registry tenant_id 已支持基本私有归属 |
| 企业内部共享 | Tenant 所有，限定 Organization/Team/Project | 授权组织/团队/项目成员 | 分享策略 + 接收方授权 + 运行时 ACL | 现有 Organization/Team/Project/RBAC 可复用；Registry 尚无明确 visibility/share binding |
| 外部伙伴交付 | Publisher/Partner 所有，按合同/许可授予 Tenant | 目录可见不代表可安装 | 伙伴发布者信任 + 平台/企业审核 + Tenant install grant | 当前 Publisher Key Trust 和安装机制提供基础；Partner/License/InstallGrant 模型未完整证明 |
| 公共资产 | 平台/社区/明确权利人 | 公共目录可发现 | 使用许可、版本兼容、Tenant 安装授权 | 未发现完整公共 Marketplace/目录主模型 |
| 企业定制分支 | Tenant 或合同约定共同所有 | 仅指定 Tenant/项目 | Tenant 自主管理，禁止默认回流公共目录 | 当前无统一 fork/provenance/ownership 关系字段 |

### 5.3 建议统一 Asset Manifest 字段

目标统一资产元数据应至少包括：

- 标识：`asset_id`、`asset_kind`、`slug`、`version`、`manifest_schema_version`。
- 所有权：`owner_type`（platform/tenant/partner）、`owner_id`、`publisher_id`、`created_by`。
- 可见性：`visibility`（private/org/team/tenant/partner/public）、`share_scope_id`、`discoverable`。
- 生命周期：`status`（draft/review/approved/published/deprecated/revoked/archived）、`published_at`、`deprecated_at`、`revoked_at`。
- 供应链：`artifact_digest`、`signature_ref`、`provenance_ref`、`sbom_ref`、`scan_report_ref`、`license_id`。
- 兼容性：`platform_api_range`、`runtime_requirements`、`dependencies[(asset_id,version,digest)]`。
- 权限声明：`permissions[]`、`data_scopes[]`、`network_scopes[]`、`secret_refs[]`、`risk_level`。
- 安装快照：`tenant_id`、`installed_by`、`install_scope`、`granted_permissions`、`license_acceptance_id`、`installed_version`、`artifact_digest`、`installed_at`。

**硬边界**：公共目录/伙伴目录只承载制品、Manifest、公开文档和扫描证明；企业知识、会话、凭据、运行黑板和业务数据留在 Tenant 运行域。发布者签名证明来源和完整性，不授予客户数据权限。安装时必须重新校验租户授权、资产状态、签名有效期和撤销状态。

## 6. 外键与租户隔离差距清单（按风险排序）

| 风险 | 证据 | 问题 | 建议 |
|---|---|---|---|
| P0：Prompt Registry 缺 Tenant | `017_prompt_registry.sql` 的 `platform_prompt_versions(prompt_id,version,...)` 和 deployment 以 prompt_id 为键 | Prompt 版本/部署指针没有 tenant_id，难以证明租户私有 Prompt 的物理隔离 | 先盘清是否仅平台级公共模板；若存在租户 Prompt，新增 tenant-scoped 版本表/兼容迁移，禁止直接改写历史键 |
| P1：RoleBinding 多态引用缺 FK | `005_identity_tenant_auth.sql`：只 FK `(tenant_id,role_id)`；subject/resource 是文本字段 | subject_id/resource_id 不保证存在，也不由 DB 保证 tenant match | 保留多态 API，但增加受控 ResourceCatalog/主体映射或按 subject_type 建分表；写入与读取均做存在性和 tenant 校验 |
| P1：Session/File/Connector project_id 无 FK | `011_registry_sessions_assets.sql` + `048_resource_project_scope.sql` | tenant_id 有界，但 project_id 是 nullable text，仅靠 Repository/ResourceScope 语义 | 增加复合 FK `(tenant_id,project_id)` → Project；历史 NULL 保持 Tenant scope；迁移前做 orphan 检查 |
| P1：Knowledge ACL / Collection Document 关系缺 FK | `006_knowledge_memory.sql` 的 `knowledge_document_acl`；`067_knowledge_catalog.sql` 的 `knowledge_collection_documents` | ACL 与 Collection link 的 document_id 没有对 Document 版本/身份建立 FK | 先明确 document identity 主表；补 `(tenant_id,document_id)` 约束或 Document identity 表，删除/归档需事务化传播 |
| P1：Run Registry Snapshot 为 JSONB | `014_run_registry_snapshot.sql`；`025_workbench_task_execution.sql` 移除旧 Agent/Model FK | 快照可记录固定版本，但数据库不能校验每个 kind/item/version 仍存在且同租户 | 新增 RunAssetReference 子表，每个引用一行并复合 FK 到 RegistryVersion；保留 JSON 快照作快速读取 |
| P1：数字员工无领域关系 | migration 001/005/011/028 全量核心关系中无 DigitalEmployee | 无岗位、组织/团队成员关系、责任人、Agent 固定版本、任务分配与绩效归属 | 新增独立 DigitalEmployee + membership/supervision/asset-binding/assignment 模型 |
| P2：Registry 创建者无 FK | `platform_registry_versions.created_by` 是 text | 无法保证创建者存在或为该租户成员 | 增加 actor_type/actor_id 的受控主体引用；审计事件保留历史身份 |
| P2：资产可见性字段缺失 | `platform_registry_versions` 无 owner_type/owner_id/visibility/license/publisher_id | 只能确认 tenant scope，不能完整表达 org/team/partner/public | 增加资产所有权与 ShareGrant/InstallGrant；平台公共目录与 Tenant Registry 分层 |
| P1：Registry kind DDL 与 Python 不一致 | `agent_platform/control_plane/registry.py` 的 `_KINDS` 包含 `routing_policy`；`011_registry_sessions_assets.sql` CHECK 仅允许 agent/workflow/tool/skill/model_provider/model_template | Python 可接受的 routing_policy 写入会被 PostgreSQL CHECK 拒绝；InMemory adapter 可能掩盖生产差异 | 统一 kind 常量与迁移约束；增加真实 PostgreSQL contract test，覆盖所有合法 kind |
| P2：InstalledExtension 缺结构化版本历史 | `platform_installed_extensions` 将 version 留在 manifest JSONB | 无法直接以数据库约束做版本索引、升级历史、回滚/授权追踪 | 增加 installed_version、digest、publisher、source、install_scope、grant snapshot 和 append-only install events |

## 7. 当前 API / 前端 / 测试证据

| 层 | 证据路径 | 本轮确认范围 | 缺口/边界 |
|---|---|---|---|
| Identity models | `agent_platform/identity/models.py` | UserRecord、Principal、Session、AuthorizationContext 等值对象 | 没有 DigitalEmployee 值对象 |
| Tenant bootstrap | `agent_platform/identity/tenants.py` | Tenant 与首个 Owner 同事务创建 | 不包含 Organization/Team/DigitalEmployee 初始化 |
| Identity Repository | `agent_platform/identity/postgres.py` | Tenant membership、角色、项目和资源授权按 tenant 查询 | 不提供数字员工成员关系 |
| Resource authorization | `agent_platform/identity/resource_scope.py` | principal/tenant/resource/permission 一致性；支持 human/API credential/service principal | 多态资源仍需调用方可信解析 |
| Registry API | `agent_platform/api/registry.py` | Registry CRUD、版本、发布、回滚、停用、active | 没有组织共享/伙伴市场 API |
| Admin API | `agent_platform/api/admin.py` | `/organization` 返回 tenants、users；`/access-control` 返回 roles/bindings/permissions | 未返回 platform_organizations、platform_teams 及 memberships |
| Organization UI | `frontend/src/components/OrganizationAccessPage.tsx` | 页面调用 admin API；组织页 tabs 仅 tenants/users；RBAC 页角色/绑定/权限 | 没有组织树、团队成员、数字员工岗位/负责人/绩效 UI |
| Extension tests | `tests/test_extension_lifecycle.py`、`tests/test_extension_manager.py`、`tests/test_extension_publishers.py`、`tests/test_extension_publishers_postgres.py` | 存在 lifecycle/manager/publisher/PostgreSQL 测试路径 | 本轮未执行这些测试；不宣称通过 |
| Identity tests | `tests/test_identity.py`、`tests/test_identity_resource_scope.py`、`tests/test_identity_api.py`、`tests/test_tenant_creation_integration.py` | 存在身份、资源作用域、API、租户创建测试路径 | 本轮未执行测试；CI 状态见 PR |
| Registry tests | `tests/test_registry_service.py`、`tests/test_registry_listing_api.py`、`tests/test_run_registry_snapshot.py` | Registry 与运行快照测试路径存在 | 本轮未执行测试；未证明完整市场/数字员工链路 |

## 8. 目标模型的关系/字段/权限验收规则

### 8.1 统一资源模型

- [ ] 所有 Tenant-owned 资源均有 tenant_id，PK/UK/FK 均包含 tenant_id。
- [ ] User 的全局身份与 TenantMembership 分离；离开一个 Tenant 不影响其他 Tenant。
- [ ] Organization/Team/Project 关系均由同租户复合 FK 约束。
- [ ] ResourceScope 在 API、Repository、Worker、Tool Gateway、Knowledge Retrieval 各入口一致执行。
- [ ] RoleBinding 的 subject/resource 引用可解析、存在且同租户。
- [ ] Task → Run → Event/Approval/Artifact 形成可验证关联；历史记录不可因主体停用被级联删除。
- [ ] Registry/Prompt/Workflow/Skill/Tool/Agent 版本不可变，Run 固定版本引用具备 FK 或等价强校验。

### 8.2 数字员工组织关系

- [ ] DigitalEmployee 是独立身份资源，不复用 User 或 Agent 主键。
- [ ] 每个 DigitalEmployee 必须有 Tenant、Organization、岗位、状态、负责人和监督/审批关系。
- [ ] Team membership 支持 User 与 DigitalEmployee 两类主体且有有效期历史。
- [ ] DigitalEmployee 对 Agent/Workflow/Skill/Tool 的绑定固定版本并支持撤销。
- [ ] 任务分配、运行轨迹、审批、产物、质量和成本可归因到 DigitalEmployee 与人类责任人。
- [ ] 停用/调岗撤销未来权限，不删除历史 Run/Approval/Audit。

### 8.3 生态资产生命周期

- [ ] Asset Manifest 统一字段、版本、所有权、发布者、许可、依赖、权限和兼容性。
- [ ] Private/Org/Team/Tenant/Partner/Public 可见性与安装授权分离。
- [ ] 发布者签名、SBOM、漏洞/许可证扫描、审核、撤销均有可追溯状态。
- [ ] Tenant 安装固定版本/digest/granted_permissions/license acceptance。
- [ ] 安装后执行隔离、审计、健康/质量/成本监控，支持禁用、回滚和卸载清理。
- [ ] 资产撤销可阻止新安装/新执行，并保留历史运行与审计证据。

## 9. 推荐演进顺序

| 优先级 | 工作包 | 依赖 | 可验证验收 |
|---|---|---|---|
| P0 | Prompt 资产租户边界确认与修复；RoleBinding subject/resource 强校验；Run snapshot 引用完整性 | 现有 Identity/Registry | 跨租户负向测试、无效主体/资源写入拒绝、Run 固定版本引用存在性测试 |
| P1 | DigitalEmployee 独立领域模型与组织/团队/监督关系 | Tenant/Organization/Team/User/AgentIdentity | 同租户复合 FK、组织调岗历史、任务归因、人工审批责任链和越权测试 |
| P1 | Asset owner/visibility/share/install grant 统一模型 | Registry + Extension + Publisher trust | private/org/team/partner/public 的发现与安装矩阵测试 |
| P1 | InstalledExtension 结构化版本/digest/授权快照/事件历史 | Extension lifecycle + Artifact registry | 升级、回滚、撤销、卸载清理与审计回放 |
| P2 | 伙伴组织/开发者门户/公共市场与许可接口 | Publisher + Asset Manifest + OpenAPI | 发布审核、许可证接受、目录发现与跨租户安装授权测试 |
| P2 | 数字员工绩效与企业运营归集 | Task/Run/Artifact/Usage/Evaluation | 绩效指标能回溯到运行证据，支持按组织/岗位/数字员工归集成本与质量 |

## 10. 本轮自检与验证边界

- 基线：`main@b622d5fc9eab1c79c28dcaa13367eec3c138679d`；以 Git Tree 递归枚举到的 PostgreSQL migration 与源码文件为审查入口。
- 已检查：Identity models/tenant bootstrap/Postgres Repository/ResourceScope、Identity/Admin/Registry API、Registry Postgres adapter、Extension Manifest/Lifecycle/Manager/Publisher/Postgres、关键 migrations 001/005/006/007/011/014/016/017/023/025/026/028/032/044/047/048/063/064/067/075，以及 OrganizationAccessPage 和对应测试路径。
- 静态一致性：三张 Mermaid 图；核心资源字段表；DigitalEmployee 目标字段/复合 FK 建议；资产生命周期、资产可见性边界、风险清单和验收项均纳入本文。
- 本轮不修改业务代码或数据库迁移；不连接真实 PostgreSQL/Redis、外部市场、伙伴系统或生产服务器；不做真机验证。
- 本轮没有在容器中 checkout 完整仓库，因此未执行 pytest 或前端构建；测试路径仅作为可追踪验证入口。PR GitHub Actions 是本次唯一 CI 执行证据，最终状态按 Checks 实际结论回填。
- 代码路径存在不等于链路已运行；本报告不将目标 DigitalEmployee 或公共 Marketplace 描述为已实现。
