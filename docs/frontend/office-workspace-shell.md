# 3D 数字办公室前端壳（UI-G2-748）

## 当前交付范围

本轮按调度补充的降级验收口径，先交付可导航的前端页面壳，不把尚未接通的 Office 后端能力伪装成已完成。

- 路由：`/office`，可从左侧“总览 → 3D 数字办公室”进入，也可直接输入 URL。
- 楼层导航：三个展示楼层，切换时清空空间选择状态。
- 空间导航：开放工位、部门工作室、协作会议室、知识中心、运维中心；选择空间后右侧检查器显示空间信息。
- 视图：空间（CSS 等距占位）、俯视、2D 降级；窄屏自动切换为单列布局。
- Agent 面板：空数组；无假 Agent、假任务、假运行状态。状态枚举图例仅用于说明未来服务端枚举。
- 空态：明确显示 Office snapshot 尚未接入；搜索输入、楼层/空间切换和检查器均可交互。
- 权限：页面没有登录前置条件或 Principal 判断；在当前静态 React 路由层可直接进入。真实 auth=false app composition/bootstrap 仍由 #755 的后端交付负责。

## 后端接入 TODO

1. 布局快照：floor/zone/desk 稳定 ID、坐标系、布局版本、占用与分配。
2. Agent snapshot：agent_id/name/role/status/current_task/current_run/model_template/usage/recent_artifact。
3. 实时事件：SSE/WebSocket、event_id + sequence 去重、断线重连后 snapshot 校准。
4. 穿透深链：Agent、工位、部门、会议室、告警、Task/Run。
5. 写操作：布局持久化、Agent 工位分配、任务分派/暂停；必须由后端授权/审批/审计，不由 UI 假造成功。
6. Three.js / React Three Fiber：当前空间视图是 CSS 交互布局占位，不宣称真实 WebGL 场景。后续在依赖和资产 manifest 确认后替换为 R3F 场景；低性能/移动端继续保留 2D 可访问视图。

## 验证边界

本 PR 的专项 frontend-ci 执行 install、lint/Prettier、TypeScript typecheck 和 production build。页面未接入真实 API，不以 mock 证明实时状态或业务穿透。无真实服务器、外部运行时 CDN、生产数据或真机验证。
