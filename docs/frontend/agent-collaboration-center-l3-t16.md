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
