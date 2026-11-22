# 登录、管理员初始化与前端灰度切换验收 — Issue #752 / #809

## 已有能力复用边界

当前 main 已包含：
- `frontend/src/components/AuthPages.tsx`：登录与首个管理员初始化表单。
- `frontend/src/lib/auth/session.ts`：调用 `POST /auth/login`、`POST /auth/initialize`、`GET /auth/me`、`POST /auth/logout`。
- `agent_platform/api/identity.py`：首个租户/管理员一次性初始化、登录会话 Cookie、登出和当前身份接口。
- `frontend/e2e/system-dual-mode.spec.ts`：双模式全站路由 smoke。

本交付不复制第二套身份组件或认证客户端。新增 E2E 覆盖初始化、登录失败/成功、深链回跳，以及 auth=false 下身份入口不可用。

## 并行构建

默认构建保持原输出目录：
```sh
pnpm --dir frontend build
# -> agent_platform/web/static
```

灰度候选构建使用独立目录，不清空/覆盖当前静态产物：
```sh
pnpm --dir frontend exec vite build --mode parallel
# -> agent_platform/web/static-parallel
```

两个构建产物都使用相同的 `/app/` public base。反向代理按流量策略决定 `/app/` 指向哪个静态根；旧版 `/platform-ui/index.html` 保持独立可访问。不要把构建产物目录切换误当成已经完成代理切换。

## 灰度策略（部署层）

1. 固定并记录旧版 `platform-ui/index.html`、当前新版 `agent_platform/web/static`、候选 `static-parallel` 的构建 SHA 与配置校验和。
2. 在开发环境部署候选静态目录，使用同源 API，验证 `/platform/v1/bootstrap`、`/auth/*`、业务 API、SSE 和文件路径。
3. 内部试用阶段由反向代理按稳定 cookie/显式测试 header 将小比例流量路由到候选目录；不要在应用内热切换 auth.enabled。对同一用户保持粘性路由，避免刷新后跨版本。
4. 逐步提升到验收环境；每一阶段记录流量比例、起止时间、构建 SHA、错误率、前端未捕获异常、API 4xx/5xx、延迟、SSE 重连和文件失败率。
5. 新入口 GO 前保留旧版入口至少一个回滚周期。不得双写业务数据；新旧前端继续调用同一后端和同一权限决策。
6. 只有验收签字后才把默认 `/app/` 入口指向候选产物。

## 回滚预案

触发条件：auth 模式与 bootstrap 不一致、P0/P1 权限/数据暴露、关键业务旅程失败、静态资源/API base path 错误、持续 5xx/错误预算超限、SSE 或文件操作大面积失败。

1. 暂停扩量并记录构建 SHA、配置校验和、时间、操作人和告警快照。
2. 仅将反向代理的 `/app/` 静态根切回上一个稳定 `static` 产物；保留 `/platform-ui/index.html` 旧入口。
3. 不回滚业务数据库、不撤销已完成 Task/Run/Approval、不清空文件或会话。
4. 验证旧版入口、API、SSE、文件上传/下载与正在运行的任务。
5. 观察稳定窗口后再决定关闭灰度；修复后必须重新执行 E2E 和灰度演练。
6. 切换 auth.enabled 不是前端灰度动作。权限模式变更必须修改部署配置并重启，false→true 先完成首个管理员和既有资源归属处理，true→false 必须显式确认隔离失效风险。

## 验收矩阵

| 场景 | auth=false | auth=true | 自动化证据 |
|---|---|---|---|
| 登录入口 | 重定向工作台，不显示登录 | 登录表单可用 | auth-cutover.spec.ts |
| 管理员初始化 | 不开放 | 仅首次初始化可成功，重复请求 409 | auth-cutover.spec.ts + identity API contract |
| 登录失败 | 不适用 | 401 显示错误，不泄露凭据 | auth-cutover.spec.ts |
| 登录成功 | 不适用 | 建立服务端会话并回到原深链 | auth-cutover.spec.ts |
| 新旧前端并行 | 新版独立产物 | 新版独立产物 | parallel build target |
| 灰度与回滚 | 代理层切入口 | 代理层切入口 | 需部署环境演练，不由浏览器 mock 代替 |

## 验证边界

- Playwright 测试通过 mock API 验证前端状态流，不证明真实数据库初始化、真实 Cookie/SameSite/CSRF、代理子路径或后端权限矩阵。
- CI 执行 `pnpm test:e2e`；本 PR 不声称本地浏览器或真机验证。
- 灰度比例、代理规则、监控阈值与回滚演练必须在具体部署环境执行；本次不申请真实服务器或外部资源，也不声称已执行现场切换。
