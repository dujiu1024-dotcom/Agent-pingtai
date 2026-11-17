# Session-backed Run persistence (C-25-02)

A Workbench Run with `platform_runs.session_id` must resolve a tenant-bound
`PostgresSessionRepository` and pass it to `LangGraphRunExecutor(session_repository=...)`.
The worker passes the persisted `session_id` and `created_by` as session context;
a session-backed Run fails closed if the executor has no repository.

## Run lifecycle

1. Before graph execution, the executor loads up to 500 newest session messages
   (returned chronologically) and idempotently appends the Run's user input.
2. The history is made available to graph nodes in
   `configurable.platform_session_history`. Agent graph state schemas can consume
   this configurable value when assembling the model context; it is not injected
   as an unknown state key.
3. When the compiled graph supports `astream`, the executor consumes
   `stream_mode="values"`. Newly emitted `messages` are appended to
   `platform_messages` as they appear. Each graph update is already checkpointed
   by LangGraph's configured durable checkpointer; the executor mirrors the
   latest checkpoint ID and state into `platform_context_window_snapshots`.
4. On success, the final assistant response and a final context snapshot are
   persisted. On cancellation/failure, the latest available checkpoint is mirrored
   before the Run is marked interrupted/failed.
5. A worker recovery loads the same session history, validates the Run's stored
   checkpoint reference against LangGraph, then invokes the graph with
   `None` input so LangGraph resumes from its persisted checkpoint. Stable
   tenant/session/Run-derived message IDs make retries idempotent.

## Boundaries

- LangGraph's PostgreSQL checkpointer is authoritative for graph execution state;
  session snapshots are an auditable session-scoped mirror, not a replacement
  checkpoint serialization format.
- Token usage is stored when the model/runtime emits validated usage metadata.
  Token budget estimation and enforcement remain owned by Issue #72.
- The history passed to a graph is exposed through configurable context. A graph
  must explicitly read `platform_session_history` in its context assembly node.
- This task does not implement Session management APIs or long-term Memory writes.
