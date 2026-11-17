# Session management API

The authenticated router exposes the following tenant-scoped operations:

- `GET /api/platform/v1/sessions`: owner-scoped list; archived sessions are opt-in via `include_archived=true`.
- `GET /api/platform/v1/sessions/{session_id}`: session metadata, recent messages and context-snapshot metadata.
- `GET /api/platform/v1/sessions/{session_id}/export?format=json|markdown`: complete message export, paged internally and capped at 10,000 messages to bound memory use. File payload bytes are not embedded; message asset IDs remain references to the versioned #8 file-asset API.
- `POST /api/platform/v1/sessions/{session_id}/archive`: active → archived.
- `POST /api/platform/v1/sessions/{session_id}/restore`: archived → active.
- `DELETE /api/platform/v1/sessions/{session_id}`: terminal soft-delete tombstone; deleted sessions cannot be restored.

Every item operation checks the authenticated tenant, session resource authorization and linked-project authorization. Delete requires the explicit `delete` action; archive/restore require `write`; read/export require `read`. Status changes use compare-and-set against the expected prior state to reject concurrent transitions. Lifecycle operations emit append-only audit events when the configured audit writer is present.

Deletion intentionally uses a tombstone rather than immediately erasing message rows or file objects. This prevents further API visibility while retaining the existing #8 asset-version lifecycle and allowing asynchronous retention/propagation policy to be applied separately. It does not claim that downstream cache/index deletion has been physically verified.
