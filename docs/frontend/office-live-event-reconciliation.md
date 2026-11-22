# Issue #748 — Office live-event reconciliation

## Existing scene reused from main

The merged Office 3D implementation is in `frontend/src/components/OfficeWorkspace.tsx` and is based on React Three Fiber / Three.js. It already provides three-floor navigation, six office zones, selectable desks and Agent markers, LOD, server-coordinate-only Agent placement, and a 2D fallback for narrow/coarse-pointer clients. The approved visual reference remains `示意图/3D办公室示意图.jpeg`.

This change deliberately extends that implementation rather than recreating the scene or the merged Office API.

## Changes in this PR

- Office snapshot responses may advertise an event stream through `events.stream_url` or `event_stream_url`. The client only connects to an explicitly advertised URL; it does not guess an undocumented endpoint.
- The existing SSE EventBus handles reconnect and `Last-Event-ID` cursor behavior. Each received event invalidates the Office snapshot query, so the UI reconciles from the authoritative snapshot instead of trusting event payloads as a source of Agent state.
- The existing 10-second snapshot refresh remains enabled as a reconciliation/fallback path when the event stream is absent or reconnecting.
- Run terminal states are normalized: `succeeded` → idle, `timed_out` → failed, `cancelled` → paused. Unknown Agent status remains neutral/offline.
- Agent metadata now retains current run ID, model template, team, department and last-active timestamp when supplied by the server.
- Unit tests cover advertised/unadvertised event stream URLs, terminal Run status mapping, unknown status and no coordinate fabrication.

## Current backend contract gap

The merged `agent_platform/api/office.py` snapshot response currently does **not** advertise `events.stream_url` / `event_stream_url`, and the Office response does not provide a dedicated persisted layout/event stream contract. Therefore this frontend can safely negotiate and consume a server-provided stream, but the deployed current response continues to use 10-second snapshot polling. This PR does not claim live push is active end-to-end.

Still outstanding for full Issue #748 acceptance:
- implement/register tenant-scoped Office SSE event endpoint and publish Agent/Run state changes with event_id, sequence/version and occurred_at;
- persist spatial floor/zone/desk layout with versioning, authorization and conflict handling;
- complete department/zone/meeting-room/alert/task click-through and permitted backend actions;
- prove snapshot recovery after stream interruption and both auth modes against real backend;
- collect target-device FPS/load/memory evidence.

Real device and external server validation are skipped per dispatch. No fake Agent records, positions, state transitions or write actions are introduced.
