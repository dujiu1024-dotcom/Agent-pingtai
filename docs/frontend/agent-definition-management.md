# Issue #757 — Agent Definition management UI shell

## Route integration

- List/editor: `/admin/agents`
- Definition deep link: `/admin/agents/:agentId`
- Both routes are part of the canonical route map and navigation.
- Route owner is #757.
- Neither route is marked `authOnly`; when the server bootstrap returns `auth.enabled=false`, the page remains navigable. The app still waits for the platform bootstrap contract and does not guess the auth mode.

## UI delivered

- Empty Agent definition list and selected-detail empty state.
- Create/edit local draft form with Agent ID, name, description, instructions, model-template reference, tool/skill/workflow references, input schema and output schema.
- Local-only validation for required Agent ID/name and JSON object syntax in input/output schemas.
- In-memory create/read/update/remove lifecycle, clearly labelled as local preview; no server resource is created or deleted.
- In-memory instruction/config version snapshots and field-level JSON diff preview.
- Draft/published/disabled status display. Publish, disable and rollback controls remain disabled until the corresponding server API and authorization contract are wired.
- Secret values are not accepted as a dedicated field; model configuration is represented by a template reference only.
- Global ErrorBoundary and shared loading/error/empty state components remain the application-level failure handling.

## API integration TODOs

The page intentionally does not call the Registry API while backend mounting, auth=false behavior, Agent-specific validation and concurrency contracts remain incomplete.

After the backend contract is ready, replace the local state adapter with the shared API client and connect:
- `GET /api/platform/v1/registry/agent` — tenant-scoped latest Agent definitions.
- `POST /api/platform/v1/registry/agent/{agent_id}/versions` — create immutable draft version.
- `GET /api/platform/v1/registry/agent/{agent_id}/versions` and version detail — version history/snapshot.
- `GET /api/platform/v1/registry/agent/{agent_id}/versions/{from}/diff/{to}` — compare versions (subject to the server's final route contract).
- Publish, disable, rollback and delete/retire endpoints — only after their final method/path, permission action, audit, idempotency and ETag/If-Match semantics are confirmed.

TODO: server-side validation must resolve model_template_ref/version and tool_refs, skill_refs, workflow_refs within the same tenant and report field-level errors. UI-only JSON parsing is not a substitute for registry/domain validation.

TODO: return revision/ETag from the API and send If-Match on edits/transitions; display 409/412 conflicts without overwriting a newer definition.

TODO: auth=false requires a supported instance-scoped Registry service and actor=null/system audit strategy. The UI does not bypass auth middleware, fabricate a Principal or use a default administrator. auth=true must continue to rely on server authorization.

## Verification

- Added Vitest route-contract tests for auth=false navigation visibility, Agent detail deep-link registration and canonical route uniqueness.
- Files were read back from the feature branch after edits.
- No local checkout, pnpm runtime or browser runner was available in this connector session; local Vitest, ESLint/Prettier, TypeScript and Vite build have not been executed here.
- GitHub Actions on the PR head are the CI source of truth.
- Real server, production Registry, model providers, secrets, external services and device testing are not part of this frontend shell increment.
