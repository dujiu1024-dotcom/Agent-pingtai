# 前端路由契约（Draft）

> Parent #742 · Page map #753。该文件定义目标态约定；在当前前端入口和路由代码盘点完成前，不代表已上线路由。

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
