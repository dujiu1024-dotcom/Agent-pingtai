# Agent Collaboration Center — L3-T16 / Issue #746

## Existing capabilities reused
The current center already reads Agent Registry definitions, recent Run snapshots and Run events, Blackboard entries/history, and Task records. Blackboard scope filtering already supports project, task and run (plus tenant/all); type filtering, entry history, evidence/source reference counts and conflict/decision workflows remain in the existing implementation. This change does not create duplicate APIs or stores.

## Added in this change
- Agent team graph/list toggle. Agents are grouped only by team/department labels explicitly present in the Registry definition. The graph does not infer collaboration edges from shared capabilities. Agent cards link to the existing Agent definition/operation route and show declared role/model/status/active-run metadata when returned.
- Workflow Run canvas/list toggle. It extracts node identities, node type, status, input/output summaries, errors and explicit parent/next references only from actual Run event payloads. Selecting a node shows the event-derived detail. Status is not inferred from the parent Run status.
- Empty telemetry state explicitly says when no node-level telemetry is available. This prevents a misleading simulated graph.
- Existing Task / Run / Project Blackboard scope switching is retained; the run selector can populate Run scope and the scope ID remains editable. Existing keyboard-accessible tabs, lists and links remain available.

## Contract gaps surfaced (not fabricated)
- Run API currently supplies snapshots and event streams. It does not guarantee a versioned executable graph snapshot or complete node/edge telemetry for every runtime. The canvas therefore renders only event-observed nodes; an event stream without node IDs yields “暂无节点级遥测”.
- The Registry Agent definition payload does not establish a normalized team membership/relationship graph contract (team ID, department ID, reporting/collaboration edges, live health and active run counts may be absent). Grouping falls back to “未分组”; no relationship edge is invented. A fully authoritative org/team graph requires the backend contract to expose these fields.
- The Issue requires pause/resume and checkpoint observation. This UI displays explicit interrupt/resume/checkpoint event records if present in the event feed; it does not trigger runtime controls or simulate those events.
- Existing event/Blackboard API authorization remains authoritative. No direct tool invocation or database writes are added. Existing Blackboard revision/challenge/decision mutations are preserved.

## Validation boundary
CI on this PR is the source of truth for lint, typecheck, unit, build and E2E. Local frontend package execution is unavailable in the connector-only environment. Real-device, production-server and external-resource checks are skipped as requested.


## L4-T103 — Workflow 节点事件实时化与 Blackboard 运行态
- Workflow Run 视图订阅现有 `GET /platform/v1/runs/{run_id}/stream`，监听 broker 的 `step_progress`、`token_stream`、`tool_output`、`run_complete` 和 `error` 事件。连接断开/游标被服务端拒绝时，重新创建 EventSource（不沿用失效游标），由 broker 重放当前保留窗口内的事件；按事件 ID/sequence 去重。保留 10 秒持久事件快照作为补偿和 SSE 不可用时的兜底。
- SSE envelope 被规范化为与持久事件列表相同的运行事件形态；快照与实时补发按事件 ID/sequence 合并去重，避免重复节点计数。
- 节点遥测识别实际 broker 的 `STEP_START`/`STEP_FINISH`/`STEP_ERROR`/`STEP_RETRY`，并兼容显式 LangGraph node 事件。没有 node_id 的运行事件不会变成节点；没有节点级状态字段/事件时继续显示“暂无节点状态”，不根据父 Run 推算。
- Blackboard 使用既有 tenant/scope 授权 SSE `GET /platform/v1/blackboard/events/stream`。事件只携带服务端允许的最小元数据（事件类型、条目 ID、版本、范围、时间）；收到后刷新列表与版本历史，不把黑板内容或证据载荷塞入 SSE。
- Blackboard SSE 连接健康时停用轮询；断线/重连期间自动恢复 20 秒快照刷新，避免实时连接失效导致页面冻结。浏览器 EventSource 按服务端游标自动重连。
- 后端 Blackboard Entry 契约仅保证 scope/scope_id、kind、content、author_id、source_refs、version、status、created_at、correlation_id。UI 不伪造不存在的 visibility、updated_at、supersedes_id 或独立 evidence locator 字段；Evidence 仍显示服务端 source_refs 引用数。
- 新增前端自动化覆盖 SSE envelope 规范化、事件去重排序、LangGraph STEP 生命周期映射和控制帧过滤。真实部署 SSE 断线/权限隔离双模式冒烟仍由目标环境验收；本轮按调度跳过真机。
