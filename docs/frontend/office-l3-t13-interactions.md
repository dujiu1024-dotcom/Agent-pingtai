# Issue #748 — L3-T13 Office interaction and live-state UI

## Scope delivered

This increment extends the existing Office workspace in place. It does not create a second Office page or duplicate the merged Office API.

- Reads the authoritative `/platform/v1/office/state` snapshot and reconciles every 10 seconds.
- Uses only an explicitly advertised `events.stream_url` or `event_stream_url`; native EventSource reconnects and each event invalidates the snapshot query so the UI re-renders authoritative state.
- Normalizes Agent identity and Run metadata from snapshot variants; unknown/missing statuses remain neutral/offline.
- Renders a selectable Agent avatar only when the server supplies both finite `x` and `z` coordinates. A desk ID alone never becomes a fabricated position.
- Adds six selectable spatial areas, clickable Agent rows with a detail inspector, selected-zone/desk detail, and keyboard-operable controls.
- Adds a responsive 2D spatial fallback and a manual 2D switch for keyboard and low-capability access.
- Caps Canvas DPR at 1.5 in the 3D view.

## API contract limitations retained

The currently merged `agent_platform/api/office.py` response does not return spatial coordinates, floor/zone/desk mappings, a persisted layout/version, Agent current status, or an Office event-stream URL. The UI therefore does not fabricate those values. Without an advertised event URL it stays on the 10-second snapshot reconciliation path. No layout writes, task dispatch, pause actions, department load, meeting Run membership, or alert-to-Run actions are exposed without corresponding server contracts and authorization.

## Verification

- Unit coverage uses `frontend/src/components/OfficeWorkspace.test.ts` for status normalization, coordinate safety, 2D fallback, and event URL negotiation.
- Playwright coverage remains in `frontend/e2e/office-workspace.spec.ts` for snapshot-driven Agent display and floor navigation.
- GitHub Actions are the CI authority for this PR. Real-device and production-server checks are skipped per dispatch.
