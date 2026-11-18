# Session, message and file workbench (Issue #762)

The existing authenticated `platform-ui` session and asset views now use only routes mounted by `agent_platform/api/sessions.py`.

## Sessions and messages
- `GET /sessions?limit=100&include_archived=true` lists visible sessions; local filtering matches ID, title, project and status.
- `GET /sessions/{session_id}` loads metadata and context-snapshot count.
- `GET /sessions/{session_id}/messages?limit=100&before_message_id=...` pages persisted messages in chronological order. Older pages are prepended using the server cursor.
- `POST /sessions/{session_id}/messages` sends a user message with optional authorized `asset_ids`.
- `GET /sessions/{session_id}/export?format=json|markdown` exports the server representation.

No assistant response or streaming state is fabricated. Live token streaming/SSE is not exposed by the current sessions router.

## Files and artifacts
- `GET /files?limit=100&session_id=...&project_id=...` filters visible assets.
- `GET /files/{asset_id}/metadata` and `GET /files/{asset_id}/versions` expose metadata and immutable version history.
- `GET /files/{asset_id}` and `GET /files/{asset_id}/versions/{version}` are fetched with same-origin credentials.
- `POST /files/{asset_id}/versions/{version}/restore?expected_version=...` restores a historical version as a new version using optimistic concurrency.
- `DELETE /files/{asset_id}` requires explicit confirmation and remains subject to server-side authorization.
- Upload uses the existing raw-body `POST /files` endpoint and server-side size/type/scanning rules.

The UI does not bypass authorization with direct object-storage URLs and does not persist message/file contents in browser storage. Upload progress is not claimed because the current endpoint is a single raw-body request.

## Verification boundary
UI contract tests assert exact route wiring, cursor use, credentialed downloads, version restore precondition and delete behavior. Module-level auth=false/auth=true smoke, browser E2E, file range/expiry checks and production storage validation remain separate acceptance work. No real server or external storage is required for these UI contract tests.
