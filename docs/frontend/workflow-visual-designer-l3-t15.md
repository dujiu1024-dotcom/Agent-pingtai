# Workflow Visual Designer — Issue #758 / L3-T15

Routes: `/admin/workflows` and `/admin/workflows/:workflowId/edit`.

## Delivered in this change

- A typed visual graph editor with Palette entries for Start, Agent, Tool/MCP, condition, parallel, join, human approval and End.
- Node selection and inspector for node ID/name/type/version, config, config schema, input/output schema, ports, permissions, risk and retry/backoff metadata.
- Edge creation/deletion with explicit source/target ports; condition and parallel/join semantics are represented as typed ports.
- Client structural diagnostics for unique IDs, one start, at least one end, dangling nodes, missing/invalid ports, self-loops, unknown node types and retry metadata.
- Graph changes serialize into the existing versioned Workflow Registry definition. Legacy nodes/edges are normalized for safe rendering.
- Registry draft save, version listing, diff/rollback routes remain the persistence/version source; no second workflow store or runtime was introduced.

## Publish / compiler contract gap

The current backend exposes tenant-authorized Registry list, draft-version create, publish, rollback, disable, active/version reads and JSON diff. It does **not** expose a node catalog, server-side schema/port compatibility validator, LangGraph StateGraph compile endpoint, compiler diagnostics endpoint, or a publish operation returning a compiled artifact bound to a pinned runtime version.

The existing Registry `publish` endpoint only changes Registry lifecycle state; it does not compile a StateGraph. Therefore this UI explicitly disables publish actions and refuses the save-and-publish mutation rather than falsely treating Registry publish as LangGraph compilation. Draft saving and version history remain available. The client structural checker is advisory only and cannot establish server schema compatibility or executable graph validity.

Required backend follow-up: versioned Node Registry + schema validator; compiler adapter mapping approved node types to registered platform callables and LangGraph StateGraph; compile diagnostics with node/edge paths; immutable compiled artifact and pinned node versions; publish/rollback binding to compiled artifact; audit events. Unknown or incompatible node versions must fail closed.

## Architecture and security boundaries

The editor only edits declarative definitions. It does not implement a scheduler, execution loop, independent state machine or checkpointer. Runtime, checkpoint, interrupt/resume and execution visualization remain owned by LangGraph/platform runtime and Issue #746. Tool execution must continue through the existing Gateway-only LangGraph adapter. Advanced Python LangGraph graphs enter through controlled registration; reverse editing is not promised.

## Validation boundary

The CI result on the PR is authoritative for lint/typecheck/unit/build/E2E. Local pnpm/browser runner is unavailable in this connector-only execution environment. Real-device, production server, external model/tool and external-resource validation are skipped as requested.
