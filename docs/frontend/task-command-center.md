# Task Command Center — Issue #745
- `/workspace/tasks`: Task Kanban、列表、筛选和按创建时间排序的时间线。
- `/operations/tasks/:taskId`: Task 字段、输入输出、Run 列表和产物引用。
- `/operations/runs/:runId`: Run 摘要、Trace ID 和执行事件时间线。
- API adapter: `GET /api/workbench/tasks`, `GET /api/workbench/tasks/{id}`, `GET /api/workbench/tasks/{id}/runs`, `GET /api/workbench/runs/{id}`, `GET /api/workbench/runs/{id}/events`; 全部经共享 API client。
- 状态分组只采用接口返回值；未知状态保留原值。progress、due_at、依赖关系没有可信来源时不展示，故不启用 Gantt。
- Run event 以 sequence、occurred_at 排序；active stream 以 5 秒轮询更新。服务端游标补偿未验证，不宣称断线无损。
- 不暴露创建/编辑/取消/重试/暂停/恢复/重分派/dead-letter 按钮，待 action/权限/幂等/审计契约核实后再接入。
- 无 mock 数据；错误、加载和空态均明确展示。真机和真实部署验证按派发要求跳过。
