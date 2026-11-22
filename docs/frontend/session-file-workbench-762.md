# Issue #762 — Session / File workbench

## Scope delivered

This PR implements the missing React workbench slice owned by #762. It does not copy the closed/unmerged #784 or #835 UI patches and does not depend on another open PR. It reuses the already registered SessionAsset API.

| UI route | Behavior | Server API |
|---|---|---|
| `/operations/conversations` | Session list, ID/title/project/status filter, archived toggle, refresh | `GET /api/platform/v1/sessions?limit=100&include_archived=...` |
| `/operations/conversations/:conversationId` | Session deep link, message timeline, cursor-based older messages, Task/Run links, attachment links, related files | `GET /sessions/{id}`, `GET /sessions/{id}/messages?limit=100&before_message_id=...`, `GET /files?session_id=...` |
| `/operations/files` | Authorized file/artifact list, filename/asset/session/project/MIME search | `GET /files?limit=100&all_visible=true` |
| `/operations/files/:fileId` | Authorized metadata, immutable version list and download per version | `GET /files/{id}/metadata`, `GET /files/{id}/versions`, `GET /files/{id}/versions/{version}` |

## Security and data handling

- All API reads use the shared `apiClient`, `etag: false`, and `cache: 'no-store'`; TanStack Query uses `gcTime: 0`. Session/message content is not written to localStorage or sessionStorage.
- Binary downloads use same-origin credentials and `cache: 'no-store'`; the UI does not mint or cache signed object-storage URLs. The version download endpoint re-authorizes file, linked session, and linked project on every request and records the server-side audit event.
- List endpoints return only records visible to the resolved Principal. The frontend does not infer tenant visibility or widen permissions. 401/403/404/410 responses are surfaced as explicit error states.
- auth.enabled behavior follows the shared Session API/identity resolver contract; this page does not fabricate a local Principal or bypass the server authorization layer.
- The page displays metadata actually returned by the API. It does not infer Task/Run/artifact associations absent from the SessionAsset response.

## Message timeline behavior

- Messages are read through the dedicated cursor endpoint, 100 per page.
- Pages are merged and sorted by the server creation timestamp and message ID to preserve chronological order across older-page loads.
- The timeline refreshes every 15 seconds. No SSE endpoint was advertised by the Session API, so the UI does not claim token streaming or SSE reconnect semantics.
- Tool/message metadata is shown as the server-returned structured metadata; Task, Run and asset IDs are navigable links.

## Validation and known limits

- Route contract tests assert list/detail route ownership and deep-link parameter resolution.
- Source review confirmed the API paths and response fields against `agent_platform/api/sessions.py` and the registered SessionAsset service.
- Local pnpm/Node test execution is unavailable in this execution context; GitHub Actions on the PR head is the executable validation authority.
- No real device, production server, or external object-storage validation is performed.
- Upload, chunked/resumable upload, malware scan UX, preview, range/expiry behavior, and end-to-end dual-mode REST/file acceptance are not added by this remaining-scope patch; they require separately advertised API contracts or the parent release gate. No such behavior is simulated.
