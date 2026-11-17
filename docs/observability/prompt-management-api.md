# Prompt version management API and Workbench integration (C-23-04)

Prompt management reuses the existing tenant-scoped Prompt Registry, PostgreSQL adapter, evaluation gate, authorization callback and authenticated Workbench shell. No separate UI application or runtime is introduced.

## API

All routes are under `/api/platform/v1/prompts/{prompt_id}`. Every route derives the tenant and actor from the authenticated Principal and checks the Prompt Manager authorizer; clients cannot select a tenant namespace.

- `POST /versions`: create a new immutable template version (`{"template":"..." }`).
- `GET /versions`: list version metadata and content hashes.
- `GET /versions/{version}`: read a specific immutable version including its template.
- `GET /versions/{version}/diff?base_version={n}`: unified diff between two versions.
- `POST /variants`: create a named variant from an existing version.
- `GET /variants`: list variants and status.
- `POST /variants/{name}/versions`: assign a new version to an active variant; history is retained.
- `GET /variants/{name}/history`: list immutable version assignments.
- `POST /variants/{name}/deprecate`: deprecate a variant; the PostgreSQL adapter rejects deprecating a deployed version.
- Existing `POST /activate` and `POST /rollback` publish/restore an immutable version through the configured evaluation gate. Existing `GET /audit` returns the change audit.

Versions are immutable by design: edit means registering a new version; version rows are never overwritten or deleted. Variant changes append history. The API returns 403 when the server-side authorizer denies access and does not trust tenant IDs supplied by the client.

## Workbench

The existing `/app` Workbench includes a Prompt Lab navigation entry. It uses the current same-origin API helper, session credentials, CSRF token, and server-provided `prompt_management` capability. It supports creating versions, listing/viewing/diffing versions, creating/updating/deprecating variants, viewing variant history and audit, and requesting publish/rollback. If Prompt Registry and its authorizer are not mounted, the capability hides the view; UI visibility is not an authorization boundary.

## Validation boundary

Focused API tests cover tenant namespace binding, version listing/detail/diff, variant create/assign/history/deprecation and denied read access. Workbench static contract tests verify integration with the existing shell and authenticated API helper. Production UI/real browser verification is skipped per task instruction.
