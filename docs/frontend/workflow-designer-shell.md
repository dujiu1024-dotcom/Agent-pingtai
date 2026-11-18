# Workflow Designer UI Shell (Issue #758)

Route: `/admin/workflows` and `/admin/workflows/:workflowId/edit`.

## Delivered shell
- Palette for Agent, Tool, condition, parallel, join, and human-approval nodes.
- Local in-memory graph editing, node properties, ports, permissions, risk, JSON config/input/output schemas, retry policy, edge creation, basic structural checks, and local snapshots.
- Explicit empty state and local-only status. Compile, publish, and server rollback controls remain disabled until contracts exist.
- Route has no `authOnly` guard and is reachable when server bootstrap declares `auth.enabled=false`.

## Backend integration TODO
- Confirm versioned Workflow Definition / Node Registry / schema validation / compiler / publish / rollback API paths and payloads.
- Persist draft and versions with ETag/revision or equivalent optimistic concurrency.
- Server must validate node type/version compatibility, typed ports, schema compatibility, cycles, parallel/join constraints, risk/permission metadata and compile diagnostics.
- Unknown or incompatible nodes must block publish. Do not infer a successful compile from client-side JSON parsing.
- Publish must create immutable versioned artifacts executed by the existing LangGraph runtime.

## Architecture boundaries
This UI edits definitions only. It does not implement a scheduler, execution loop, state machine, or checkpointer; Run state, checkpoint, interrupt/resume and runtime visualization remain owned by LangGraph/platform runtime and Issue #746. Advanced Python LangGraph graphs enter through controlled registration; no promise of lossless reverse editing.

## Validation boundary
No local pnpm/browser runner is available in the connector session. The PR includes route contract tests; GitHub Actions is the automation validation source. No real server, production graph, external resource, or device was used.
