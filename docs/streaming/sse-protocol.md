# SSE protocol layer

The agent_platform.streaming package defines the transport contract for Server-Sent Events. It is independent of Agent execution and Blackboard state production.

## Event envelope

Each application event is emitted as an SSE frame with an event name, optional replay cursor (id), optional reconnect hint (retry), and one JSON data object. Supported event names:

- token_stream
- step_progress
- tool_output
- run_complete
- error

The data object is the event-specific payload. Producers should include stable correlation fields such as run_id and a monotonically increasing sequence where available. The protocol layer does not invent identifiers or claim replay durability.

## Heartbeats and response headers

iter_sse emits an SSE comment heartbeat every configured interval while awaiting the next source event. Comments do not dispatch browser message events. sse_response sets text/event-stream, disables intermediary buffering, and sends no-cache headers.

## Usage

    from agent_platform.streaming import SSEEvent, sse_response

    async def events():
        yield SSEEvent("step_progress", {"run_id": "run-123", "step": "plan"})
        yield SSEEvent("run_complete", {"run_id": "run-123", "status": "completed"})

    return sse_response(events(), heartbeat_interval=15)

This task adds protocol primitives only. Connecting model token streams, execution steps, tool callbacks, persistent replay/Last-Event-ID, and Blackboard subscriptions belongs to separately scoped follow-on tasks.
