# Issue #743 前端基座验收补充

本文件记录 L2-T09 本轮补齐的自动化验收入口；不替代 #743 的完整 DoD，也不将 mock 服务端响应当作后端集成证据。

## 运行方式

在 `frontend/` 目录执行：

- `pnpm test`：Vitest 单元/组件测试（Playwright E2E 不纳入 Vitest 收集）。
- `pnpm test:e2e`：Playwright Chromium 桌面（1440×900）与平板（768×1024）项目。
- `pnpm lint`、`pnpm typecheck`、`pnpm build`：格式/静态类型/生产构建。

CI 工作流为 `.github/workflows/frontend-ci.yml`，安装锁定的 pnpm 与 Node 版本，运行上述门禁并上传 Playwright 报告。

## 本轮 E2E 覆盖

`e2e/issue-743-foundation.spec.ts` 覆盖：

1. 根入口重定向到页面地图 canonical 驾驶舱，且读取一次服务端 bootstrap。
2. `auth.enabled=false` 下无登录深链直达业务路由，不请求 identity/session API；访问登录入口回到驾驶舱。
3. `auth.enabled=true` 下显示条件登录入口。
4. 桌面和平板视口下主导航、搜索、通知入口可见且无水平页面溢出。

## 契约边界

- E2E 对 bootstrap 使用浏览器路由 stub，只验证前端消费服务端配置的行为，不证明真实后端部署配置。
- 不创建匿名 principal，不在前端覆写 `auth.enabled`，不伪造用户、角色、权限或数据范围。
- API Client 的分页、错误 envelope、request_id、ETag/304、幂等键、revision conflict、取消与超时由 `src/lib/api/api-client.test.ts` 覆盖。
- SSE 事件解码、去重、实体 sequence、重连游标与尾帧由 `src/lib/events/event-bus.test.ts` 覆盖；Query Cache 同步由 `src/lib/events/query-event-bridge.ts` 实现。
- 设计 Token 与可运行组件样例分别位于 `docs/frontend/design-system.md`、`src/components/DesignSystemShowcase.tsx`。参考图仅作为视觉方向，不代表生产数据。

## 未由本轮声称通过的验收

真实服务端 REST/SSE/WebSocket/文件双模式访问、真实 CSRF/session/RBAC 权限一致性、反向代理部署及回滚、NVDA/VoiceOver 屏幕阅读器实测仍需对应后端/部署环境证据。按调度要求跳过真机、真实服务器及外部资源验证。
