# 前端路由契约（Gate 0 冻结）

> Parent #742 · Page map #753。该文件冻结目标态路由语义；当前代码状态仍以 page-map.md 的 verified / conditional / module-only-unregistered / missing / proposed 标记为准。

## 1. 命名
- 路由统一小写 kebab-case，资源使用复数集合名，详情使用稳定主键：`/operations/tasks/:taskId`。
- 路由参数使用 camelCase（如 `:taskId`），query 使用 camelCase。
- 路径不包含展示名称、邮箱、模型密钥或其他可变/敏感数据。
- 管理定义与运行实例分开：`/admin/workflows/:workflowId/edit` 与 `/operations/workflow-runs/:runId`。

## 2. Query 标准
| 参数 | 类型 | 用途 |
|---|---|---|
| q | string | 搜索词 |
| status | string 或重复参数 | 状态过滤，枚举以服务端 schema 为准 |
| projectId / agentId / modelTemplateId | string | 资源过滤 |
| from / to | ISO-8601 string | 时间范围 |
| sort | string | 稳定字段名，可选方向按约定编码 |
| page | positive integer | 页码，从 1 开始 |
| pageSize | positive integer | 分页大小，受服务端上限约束 |
| tab | string | 详情页子视图 |
| nodeId | string | Run/Workflow 图节点定位 |
| fromPage | string | 来源 page_id |
| returnTo | same-origin relative path | 返回列表位置；必须校验为站内相对路径 |

## 3. 深链与返回状态
- 所有详情路由可直接打开、刷新和复制分享。
- 从列表进入详情时保存筛选、排序、分页和滚动位置；返回列表恢复上下文。
- `returnTo` 只允许站内相对路径，拒绝协议、双斜杠、反斜杠和编码绕过。
- 未提供 returnTo 时按实体默认列表返回；不能依赖浏览器历史中一定存在上一页。
- 搜索、通知、驾驶舱穿透统一携带实体主键，不传展示名作为定位依据。

## 4. 面包屑
- 由路由元数据定义，不从页面标题猜测。
- 列表：工作空间 / 模块 / 列表；详情：工作空间 / 模块 / 列表 / 实体名。
- Agent/Workflow/Model 定义页归平台管理；Task/Run/Approval 等运行业务页归智能体运营。
- 面包屑名称异步加载失败时显示稳定资源类型，不阻断页面。

## 5. 权限与路由守卫
- 路由守卫只消费服务端启动配置和权限结果。
- auth.enabled=false：不重定向登录、不生成匿名用户、不要求 token；用户/角色/RBAC 路由隐藏且不可成为业务路由前置条件。
- auth.enabled=true：未登录重定向登录；已登录但无权显示标准 403；服务端 API 始终再次授权。
- 前端隐藏入口不构成安全边界。REST、SSE、WebSocket、文件下载规则必须与服务端运行模式一致。

## 6. 错误与未知值
- 主键不存在：标准 404；无权访问：403（仅权限启用模式）；服务端不可用：可重试错误页。
- 未知状态枚举显示中性标签和原始值（若安全可见），不得错误映射为成功/失败。
- 非法 query 参数忽略并回退默认值；页码越界由服务端/客户端一致回到最后有效页。

## 7. 路由变更门禁
- 新增/改名/删除路由必须同步修改 page-map.md。
- PR 描述列出受影响 page_id、实体主责和兼容重定向。
- 测试覆盖路由唯一性、详情主责唯一、深链刷新、返回筛选恢复、两种权限模式。
- 未经评审不得将旧路径直接复用为语义不同的新实体路由。


## 8. 当前代码路由事实与目标路由迁移

当前生产工作台入口为 `/app`（`agent_platform/web/app.py`），内部页面使用 `data-view` 状态切换，不是 URL 路由。当前主导航视图包括 overview、board、tasks、sessions、replay、batch、artifacts、approvals、registry、prompt-lab、knowledge-graph、governance、observability。详情视图目前主要由 JS 内存状态控制，刷新/分享深链能力不应视为已有。

`platform-ui/index.html` 是另一个旧版单文件工作台。新 React 工程尚未在已检查的根目录、frontend/、web/、agent_platform/web/ 中发现 package.json；新工程与目标路由均属于待实施。

后续路由迁移必须在 page-map.md 中为每个旧视图登记：
- 旧入口：`/app` + view key 或旧 `platform-ui` 对应页面；
- 新 page_id 与目标 path；
- 是否提供兼容跳转、是否需要保留旧入口；
- 深链/详情 ID/query 参数迁移规则；
- 回滚路径。

特殊注意：旧版 3D 办公室已于 2026-11-19 退役，`/office`、`/app?legacy=true` 回退和 `/api/platform/v1/legacy/*` 均不再由平台提供；`/office` 返回 404，`/app?legacy=true` 直接返回平台 React 前端。3D 视图统一使用 `/app/workspace/office`。


## 9. 路由状态与唯一性约定

路由状态必须使用以下枚举，页面地图中的目标态路由不能直接视为服务已注册：

| 状态 | 定义 | 验收证据 |
|---|---|---|
| `verified` | 当前组合入口/路由源码明确注册，且存在可定位 handler | 注册文件、include_router/route 声明 |
| `conditional` | 源码存在注册逻辑，但需 service/resolver/authorizer/capability 条件满足 | 组合参数与条件分支 |
| `module-only-unregistered` | router/handler 模块存在，但当前 app composition 未注册 | 模块文件 + 组合入口缺失 |
| `missing` | 已检查组合入口和模块，没有可证实的 API/路由 | 搜索范围与责任 Issue |
| `proposed` | 目标页面/路由契约，当前尚未实现 | page-map page_id 与主责 Issue |

页面 UI 路由与 API endpoint 状态分开记录。静态 HTML 返回入口（如 `/app`）不等于内部 `data-view` 已有独立 URL；一个 router include 也不等于其中每个可选 endpoint 在当前实例都已挂载。

## 10. 页面身份与参数注册表

- `page_id` 是页面永久标识，不随中文标题、导航分组、组件布局变化；删除后不得复用。
- 一个 page_id 对应一个规范 path。别名/旧入口通过迁移表指向该 page_id，不建立重复详情页。
- 同一主实体的详情必须由一个 page_id/path 唯一负责；跨模块卡片只链接到该详情，不复制 CRUD。
- path 主键使用后端稳定 ID：Task `taskId`、Run `runId`、Agent `agentId`、Workflow Definition `workflowId`、Provider `providerId`、Template `templateId`、Routing Policy `policyId`、Tool/MCP `toolId`、Skill `skillId`、Knowledge Base `knowledgeBaseId`、Document `documentId`、Chunk `chunkId`、Memory `memoryId`、Conversation `conversationId`、File/Artifact `fileId`、Approval `approvalId`、Dataset `datasetId`、Evaluation Run `evaluationRunId`、Audit Event `eventId`。
- path 参数只放资源定位 ID；版本号如需定位不可变定义版本，使用 `version` query 或版本子路由，且在所属模块字段/API 契约中确认。

## 11. Query 参数规范

| 参数 | 类型 / 语义 | 适用范围 | 规则 |
|---|---|---|---|
| `q` | string，搜索词 | 列表/全局搜索 | trim；不得承载密钥或隐私字段 |
| `status` | string 或重复值 | Task/Run/Agent/Approval 等列表 | 枚举以 API schema 为准；不认识的值保留为 unknown，不映射成功 |
| `projectId`、`agentId`、`modelTemplateId` | string，过滤主键 | 任务/运行/成本列表 | 使用稳定 ID，不使用展示名称 |
| `from`、`to` | ISO-8601 datetime | 时间列表/趋势下钻 | 必须带 Z 或 offset；内部统一转 UTC，展示使用本地时区 |
| `sort` | string | 列表 | 使用 schema 允许字段；方向约定为 `field:asc` / `field:desc` |
| `page` | positive integer | 分页列表 | 从 1 开始；非法值回退 1 |
| `pageSize` | positive integer | 分页列表 | 正数且不超过服务端上限；超限按服务端响应处理 |
| `tab` | string | 详情/复合页面 | 只接受页面登记的 tab key；未知值回退默认 tab |
| `nodeId` | string | Run/Workflow 图 | 必须属于当前 run/workflow；无效时不阻断详情 |
| `fromPage` | page_id | 穿透来源 | 只接受已登记 page_id |
| `returnTo` | same-origin relative path | 返回列表 | 必须拒绝 scheme、authority、双斜杠、反斜杠、CRLF 和多次编码绕过 |
| `version` | string/integer | 版本化定义详情 | 必须由 API contract 定义版本语义，不允许前端自行推断 |

多值筛选应使用重复 query key（例如 `status=queued&status=running`），禁止同一字段混用逗号分隔和重复 key。未知 query key 不得改变资源定位或授权结果；可忽略并记录诊断。筛选条件不得包含凭据、访问令牌、Prompt 正文或其他秘密。

## 12. 深链、返回状态与导航

1. 所有目标态详情 path 支持直接打开、刷新、复制和新标签页访问；服务端静态托管必须为已登记 SPA path 提供应用 fallback，但 API path/未知资源不能被 fallback 吞掉。
2. 从列表进入详情时，优先将筛选、排序、分页、时间范围编码为可分享 query；滚动位置可由客户端历史状态保存，不写入业务 API 参数。
3. `returnTo` 优先采用同源相对路径；缺失/非法时回退到实体默认列表，不能依赖浏览器 history.back()。
4. Task → Run：使用 Task 的稳定 `taskId` 与 Run 的稳定 `runId`；Run 详情可带 `fromPage`、`returnTo`，不得把 taskId 填入 runId。
5. Agent → Model：Agent Definition 页面引用 `modelTemplateId` 并跳转模板详情；Provider/Template/Routing Policy 是三个独立详情归属。
6. Workflow：定义编辑 path 属 #749；运行态 path 属 #746；二者不可共用 `:id` 造成主责混淆，运行详情用 runId，定义详情用 workflowId。
7. Evaluation：指标聚合页面跳转到 #750 的 Dataset/Evaluation Run/Case 事实详情；#747 不持有样本详情。
8. 3D Office：Agent/Task/Run 点击事件只导航到其唯一详情页；`/app/workspace/office` 是平台唯一的 3D 办公室入口，旧 `/office` 已下线。
9. 面包屑由 page_id 的路由元数据构造；详情标题加载失败时显示资源类型与稳定 ID，不阻断返回导航。

## 13. 双权限模式路由可见性与直达行为

| 路由类别 | auth.enabled=false | auth.enabled=true |
|---|---|---|
| 工作空间/运营业务页 | 直接进入；不要求 session/token/Principal | 先完成登录，再由服务端 RBAC 决定 |
| 平台资源定义（Agent/Workflow/Model/Tool/Skill） | 可见；不执行用户/角色/数据范围过滤 | 按服务端资源/操作权限控制 |
| 组织/成员/角色/RBAC 管理 | 导航隐藏，直达返回“不适用/不可用”页面，不得要求初始化身份 | 仅授权管理员/角色可见和操作 |
| 登录/退出/会话/首个管理员初始化 | 不显示、不调用、不重定向；初始化不适用 | 登录可用；初始化仅 auth=true 且未初始化时开放；不提供公开注册 |
| 全局搜索/通知/审计/运维 | 业务可见性依能力与 API；不要求身份门槛 | 菜单和数据由 RBAC 决定 |
| 无权限页面 | 不得制造身份授权门槛；服务端业务错误仍按真实语义展示 | 标准 403，不得把 401/403 混为登录成功 |
| REST/SSE/WebSocket/文件 | 必须与 no-auth API policy 一致；不得由 UI 单独绕过 | 服务端认证、资源授权、审计 |

模式只由服务端启动配置确定；前端 bootstrap 只读消费。配置变更需重启，不提供前端热切换开关。当前主干仍强制 Identity middleware 的事实记录在 page-map CODE-AUTH-001；在 #755 交付前，不得宣称 auth=false 已可运行。

## 14. 维护、链接检查与路由约定自检

每次路由相关 PR 至少执行并记录以下自动化检查（可由脚本或测试实现，Gate 0 文档先定义契约）：

- Markdown 本地链接：相对路径目标存在；仓库内图片引用目标存在；外部链接格式合法。
- Mermaid：代码围栏成对，节点引用有效；不允许将 Mermaid 作为唯一事实源，配套表格必须存在。
- page_id：非空、全局唯一；旧 view key/alias 不得重复注册 page_id。
- canonical path：非空、目标路由无冲突；path 参数名稳定；重复实体详情归属失败。
- ownership：每个页面恰有一个主责 Issue；共享入口必须拆分到明确的 page_id，不得双主责。
- API mapping：每页至少引用一个 API contract ID，或明确标记 `not-applicable` / `missing`；不得把 module-only-unregistered/conditional 误判为 source-registered。
- mode matrix：每页明确 auth=false 与 auth=true 可见性/直达行为；登录、组织和 RBAC 页 auth=false 不可见。
- legacy crosswalk：所有 /app data-view key 与 platform-ui 页面均可映射到唯一 page_id 或明确的复合入口拆分规则。
- 视觉引用：WS-OVERVIEW 只引用首页基准图；WS-OFFICE 只引用 3D 办公室基准图；资源缺失应报错或登记缺口，不允许静默换图。

路由约定自检失败时，PR 不得宣称 Gate 0 冻结。文档修改只改变结构契约，不会自动注册任何页面或 API。
