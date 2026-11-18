# Memory governance workbench (Issue #761)

The existing `platform-ui` memory view now exposes the fields returned by the mounted memory API without inventing additional server capabilities.

## Read paths

- `GET /api/platform/v1/memories`: visible records, including memory ID, scope/scope ID, content, source reference, confidence, version, status and expiry.
- `GET /api/platform/v1/memories/candidates`: reviewable candidate/conflict records.
- `GET /api/platform/v1/memories/{memory_id}/history`: authorized version history with source reference, metadata and recorded timestamp.

The UI filters the returned list locally by ID, scope, source and status. It renders unknown status values verbatim and does not map them to a fabricated lifecycle state. Detail view reuses the supported list endpoint; it does not assume a per-record GET endpoint exists.

## Mutations

- Candidate confirmation uses `POST /memories/{id}/confirm`.
- Archive uses `POST /memories/{id}/archive` and is offered only for records whose returned status is `active`.
- Forget uses `DELETE /memories/{id}`; the UI requires explicit confirmation.
- All calls use the shared same-origin API helper, preserving its CSRF behavior.

No edit, reject, revoke, retention-policy mutation or conflict-resolution control is presented in this increment. Those operations require a separately verified UI contract and must not be inferred from domain-service methods.

## Provenance and privacy

The list and detail views expose the canonical `source_ref`, scope, version, confidence and expiry when supplied. Version history shows recorded time and a side-by-side comparison of the latest two content snapshots. Content is escaped before insertion into the DOM; it is not copied into local storage or another persistent browser cache. The UI only renders fields actually returned by the API.

## Verification boundary

The added UI contract test asserts the page controls, supported read/write paths, provenance fields and unknown-state handling. Automated execution must be confirmed by the PR's GitHub Actions checks. No real server, production memory store or device/browser session is part of this change.
