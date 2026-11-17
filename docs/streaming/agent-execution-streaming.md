# Agent execution streaming

C-30-02 connects Run execution to the SSE primitives from C-30-01.

## Flow

1. The workbench worker resolves the immutable Agent/model registry snapshot and creates a `LangGraphRunExecutor`.
2. The executor publishes Run lifecycle and graph callback events to a tenant-scoped `RunEventBroker`.
3. LangGraph `astream_events(version="v2")` is consumed for node/Step, chat-model and Tool events. Chat-model stream chunks are emitted as `token_stream` events.
4. The Run-scoped model client reads the context-local token callback. Its pinned model router passes that callback to the OpenAI-compatible transport, which sends `stream=true`, parses provider SSE chunks, emits each content delta immediately, and returns the accumulated completion plus provider usage to the existing audit/quota settlement path.
5. The authenticated `GET /api/platform/v1/runs/{run_id}/stream` endpoint sends the broker events using the C-30-01 SSE response helper. The Workbench uses browser `EventSource` to append tokens and render Step/Tool progress. Browser reconnects automatically send `Last-Event-ID`.

## Event alignment

The SSE envelope names remain the C-30-01 names:

- `token_stream`: one model content delta
- `step_progress`: `event_type` is one of RUN_START, STEP_START, STEP_FINISH, LLM_REQUEST, LLM_RESPONSE
- `tool_output`: Tool result; the payload also carries TOOL_OUTPUT as its event_type
- `run_complete`: terminal COMPLETE event
- `error`: execution ERROR event

The payload includes `run_id`, a monotonic per-Run `sequence`, and the #29 event-model `event_type`. Tool calls are represented as `step_progress` with `event_type=TOOL_CALL`; tool results use `tool_output`.

## Host wiring and deployment boundary

The same tenant-aware event-broker resolver must be injected into both `RegistryLangGraphExecutorResolver(event_broker_resolver=...)` and `create_platform_app(run_event_broker_resolver=...)`. The included `RunEventBroker` is a bounded in-process implementation for tests and single-process deployments. Multi-process production deployments must inject a shared broker/pub-sub adapter so worker publications reach API subscribers; this task does not introduce a second persistent event store.

The graph factory must use the Run-scoped model client from the registry runtime and expose LangGraph's `astream_events` interface. Existing routing audit, immutable model pinning, quota reservation/settlement, usage recording, tool Gateway enforcement, Run leases, and Session persistence remain in force. The SSE route performs the normal Run read authorization before subscribing.

## Non-goals

No Blackboard subscription/push integration (C-30-03), durable replay/event-store implementation, or performance/real-device acceptance is included. The broker retains a bounded reconnect window in process; cross-process replay and production durability require the host's shared event adapter.
