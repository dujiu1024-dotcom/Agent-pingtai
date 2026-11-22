# Task Command Center — Issue #745
- `/workspace/tasks`: Task Kanban、列表、筛选和按创建时间排序的时间线。
- `/operations/tasks/:taskId`: Task 字段、输入输出、Run 列表和产物引用。
- `/operations/runs/:runId`: Run 摘要、Trace ID 和执行事件时间线。
- API adapter: `GET /api/workbench/tasks`, `GET /api/workbench/tasks/{id}`, `GET /api/workbench/tasks/{id}/runs`, `GET /api/workbench/runs/{id}`, `GET /api/workbench/runs/{id}/events`; 全部经共享 API client。
- 状态分组只采用接口返回值；未知状态保留原值。progress、due_at、依赖关系没有可信来源时不展示，故不启用 Gantt。
- Run event 以 sequence、occurred_at 排序；active stream 以 5 秒轮询更新。服务端游标补偿未验证，不宣称断线无损。
- 不暴露创建/编辑/取消/重试/暂停/恢复/重分派/dead-letter 按钮，待 action/权限/幂等/审计契约核实后再接入。
- 无 mock 数据；错误、加载和空态均明确展示。真机和真实部署验证按派发要求跳过。


## L3-T10 Round 3 补充：可配置列表与时间线边界

- 列表支持项目、状态、优先级、Agent、创建人、创建时间范围筛选，以及创建时间/更新时间/耗时排序（升/降序）；查询条件保存在 URL。标签筛选仅在后端返回可信 tags 字段时生效，当前 TaskRecord schema 未包含 tags，不渲染标签筛选控件。
- 列表列通过 URL 参数 `columns` 配置，支持任务名称（固定必选）、状态、优先级、项目、负责 Agent、创建人、创建/更新时间、耗时、最近 Run、Trace ID；刷新/复制 URL 可还原列配置。
- 默认列保持兼容；非法列名去重并剔除，任务名称列始终保留。列配置仅影响展示，不改变服务端返回字段和权限。
- Task 时间线按真实 `created_at` 排序；Run 详情沿用真实 `/runs/{run_id}/events` 事件流，按 sequence 优先、occurred_at 和 event_id 稳定排序，5 秒刷新并分页。
- 当前后端 TaskRecord schema 有 created_at/started_at/finished_at，但没有可验证的依赖关系与 due_at 字段；因此 Gantt 仍明确禁用，不使用 created_at 冒充计划开始时间、不推算依赖线。拿到依赖和计划时间契约后再启用。
- Task 与 Run 深链均为路由级页面，可刷新恢复；详情包含任务输入/输出、Run 列表、trace_id、错误、交付物引用，以及 Run 事件/审批/工具副作用/日志区域（按 API 返回情况展示）。
- 新增列配置模型测试，覆盖默认值、非法值去重和必选标题列。


## L4-T102 — Run 事件断线补偿
- Run 详情订阅真实 `GET /platform/v1/runs/{run_id}/stream` SSE 端点，按 broker 的 `Last-Event-ID` 游标重连；不会伪造服务端事件或序号。
- 收到 SSE 帧后以 `event_id` 去重，并按服务端 `sequence` 优先排序；初始/兼容快照与实时事件合并，避免 5 秒快照刷新覆盖实时补发事件。
- 当服务端返回 400 且已有游标时，视为游标超出保留窗口：清除游标、重新读取事件快照，再从当前流重新订阅。
- SSE 不可用/部署未挂载时保留既有 5 秒事件列表轮询作为兼容路径；页面显示实时连接、恢复中或轮询兼容状态。
- 本地自动化覆盖 SSE 帧解析、非法载荷处理、重放去重和 sequence 排序；真实部署断线/过期游标端到端验收仍需目标环境执行。
