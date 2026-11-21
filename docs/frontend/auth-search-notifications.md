# Auth、全局搜索与通知交互

## 双模式启动约束

- `/platform/v1/bootstrap` 是唯一模式来源。未成功读取配置时保持启动错误态，不以构建变量、local storage 或前端默认值推断模式。
- `auth.enabled=false`：AuthProvider 不调用 `/auth/me`，不创建匿名 principal，不显示登录/初始化/用户菜单；登录和初始化深链重定向到默认驾驶舱。
- `auth.enabled=true`：AuthProvider 读取 `/auth/me`；401 视为未登录并跳转登录页；登录成功后重新读取会话，登出后清理服务端 Query 缓存和 ETag validators。
- 会话在页面重新获得焦点/可见时重新校验；临时网络错误不直接销毁已确认会话，服务端 401 才清空会话。
- API 返回的 session 字段以当前前端已确认的 `tenant_id`、`principal_type`、`user_id`、`email`、`display_name`（`/auth/me` 顶层字段），以及 service-account 分支的 `service_account_id`、`agent_id` 为准。`POST /auth/login` 响应提供 `expires_at` 与 `csrf_header: X-CSRF-Token`；`/auth/me` 当前不返回 expiry，前端仅保留本次登录响应中的过期时间并在到期时向服务端复核，不把它持久化为身份凭据。CSRF 令牌由服务端 `__Host-platform_csrf` cookie 提供，统一 API Client 读取后提交 `X-CSRF-Token`。

## 搜索与通知

- 全局搜索复用既有 `GET /platform/v1/search?q=&limit=30` 契约；弹层和完整搜索页共用同一 TanStack Query cache key。
- 通知弹层复用既有 `GET /platform/v1/notifications` 契约和完整通知中心数据；每 30 秒刷新，弹层只展示最多 6 条最近项。当前响应契约未确认已读/未读及单条标记已读 API，因此不伪造未读数量或写操作。
- 全局搜索快捷键为 Ctrl/Cmd+K，Escape 关闭；弹层结果沿用服务端返回的实体路径，不在前端拼装未知详情 URL。
- 本任务不增加外部身份提供方、WebSocket 服务端或搜索/通知后端接口。
