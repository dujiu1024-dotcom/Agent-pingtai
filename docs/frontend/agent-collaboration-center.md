# Agent collaboration center (UI-G2-746)

Route: `/workspace/collaboration`.

## Data contracts used

- Agent directory: `GET /api/platform/v1/registry/agent` (registry item envelope; name/role/description/model template are read from the published definition when present).
- Workflow observation: `GET /api/platform/v1/runs/{run_id}` and `GET /api/platform/v1/runs/{run_id}/events?limit=200`. A Run ID is explicitly supplied by the operator; events are sorted by sequence when available, then occurrence time.
- Blackboard: `GET /api/platform/v1/blackboard/entries?scope={project|task|run|tenant}&scope_id=...&limit=100`; entry history: `GET /api/platform/v1/blackboard/entries/{entry_id}/history`. The API enforces the caller's scope authorization.

## Deliberate limits

- This is an operational view, not an Agent or Workflow definition editor. Definition CRUD and publishing belong to Issue #749.
- The current public Runs API exposes Run snapshots and event records, but the UI does not fabricate a LangGraph node/edge graph. If node-level telemetry is absent, the page reports the absence rather than deriving node success from the parent Run.
- Registry definitions do not establish live health, active run counts, or team/department relationships. Those values are not invented.
- Blackboard entry content is summarized in the timeline; source reference identifiers are shown as returned. Resolving them to document chunks, messages, or files requires the corresponding authorized source locator API.
- No write actions, tool execution, or database mutation are exposed.

## Verification boundary

The frontend CI is expected to run on the pull request. No real server or physical-device validation was performed, as required by dispatch. Auth-enabled and auth-disabled live smoke testing remains environment-dependent; all API reads continue through the authenticated platform API and its server-side scope policy.
