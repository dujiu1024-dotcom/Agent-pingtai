# UI-G1-743-d：路由、导航与全局入口

## 路由事实来源

路由注册以 [Gate 0 页面地图 PR #754](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/754) 的 page_id/path/主责定义为准。React Router 6 使用稳定 path 参数；根路径与新前端兼容入口 `/app` 只做重定向，不改变旧 `platform-ui/` 或后端旧 `/app` 静态入口。

默认首页为 `/workspace/overview`（WS-OVERVIEW）。实体深链采用页面地图约定，例如：

- Task：`/operations/tasks/:taskId`
- Run：`/operations/runs/:runId`
- Agent Runtime：`/operations/agents/:agentId`
- Approval：`/operations/approvals/:approvalId`
- Document：`/operations/documents/:documentId`

导航按三段组织：工作空间、智能体运营、平台管理。全局搜索与通知中心是顶栏全局入口，不复制各模块实体详情。

## 权限模式

唯一事实来源是服务端公开 bootstrap：`GET /api/platform/v1/bootstrap`，响应字段 `auth.enabled`。前端使用统一 API Client 请求 `/platform/v1/bootstrap`（Client 默认 API base 为 `/api`），不读取 `VITE_AUTH_ENABLED` 等前端开关。

- `auth.enabled=false`：bootstrap 成功后根路径直接进入运营驾驶舱；不显示登录/初始化入口；组织、成员与 RBAC 导航隐藏，直达时返回“不适用”状态；业务路由不因 token 缺失被前端阻断。
- `auth.enabled=true`：身份入口、组织与 RBAC 导航按模式开放；真实会话、登录与每次资源授权由后端 Identity/RBAC 契约负责。本任务不模拟身份、不创建 Principal，也不把前端路由隐藏当作安全边界。
- bootstrap 请求失败或缺少布尔型 `auth.enabled` 时，显示可重试错误状态；禁止猜测默认为 false 或 true。

后端 bootstrap endpoint 的实现位于 #755 PR #771（独立后端 PR，未由本任务合并）；该 PR 必须与此 UI 分支按部署依赖顺序集成。此处只消费其公开响应契约。

## 搜索与通知边界

- 全局搜索页面当前可检索本地已注册页面的标题、分组和 page_id，支持 Enter 与 Ctrl/⌘+K 快捷入口。
- 跨实体 Search API 在 #753 页面地图中登记为待核实（API-SEARCH）；不调用臆造的 endpoint，不将本地页面检索伪装成实体搜索。
- 通知中心入口已注册，但 Notification list/read/SSE API 在 #753 中仍待核实（API-NOTIFICATION）；空态明确说明缺口，不展示假通知、不建立未经契约确认的事件连接。

## 状态与验证

- Loading：读取 bootstrap 时展示加载状态。
- Error：bootstrap 失败提供重试；React render 异常由全局 ErrorBoundary 捕获。
- Empty：业务模块尚未交付时使用标准空态，不伪造业务数据。
- 404：未知 URL 显示标准 Not Found 与驾驶舱返回入口。
- 响应式：桌面左侧三段导航；平板可折叠；窄屏导航横向滚动；支持键盘焦点与 reduced-motion。
- 单测覆盖 bootstrap schema、两种 auth 模式导航可见性、三段导航结构、page_id/path 唯一性和实体深链参数。
