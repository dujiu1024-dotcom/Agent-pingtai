# Run Replay API (C-29-02)

Replay reads the immutable Run trace from C-29-01 and reconstructs execution context from persisted Run context snapshots. Phase, version, memory snapshot reference, registry versions and step inputs are never substituted with defaults.

Configure `run_replay_resolver` in `create_platform_app`. The resolver returns a tenant-scoped `RunReplayService` built with the Run lifecycle store, C-29-01 trace repository, a `RunContextSnapshotService` resolver, and optional registry/memory snapshot resolvers and a controlled step rerun callback.

The persisted context must contain non-null `phase`, `version`, and `memory_snapshot_ref`. Missing snapshots or fields fail closed with HTTP 409. Registry versions and memory content are loaded only from persisted references.

- `POST /api/platform/v1/runs/{run_id}/replay`: reconstruct context and open a replay session.
- `POST .../replay/{session_id}/step`: advance one persisted event.
- `POST .../pause` and `POST .../resume`: stop/continue stepping.
- `POST .../fast-forward`: move to the end of the persisted timeline.
- `POST .../back` or `POST .../seek`: move the cursor backward or to an exact sequence.
- `POST .../rerun`: invoke the configured runner only when the selected persisted event has a step ID and durable `replay_input` or `replay_input_ref`.

Every endpoint uses the existing Run authorizer and tenant principal. Replay cursors are process-local transient controls; timeline and reconstruction inputs remain durable. Cross-worker cursor continuity requires a shared session store and is outside this task.
