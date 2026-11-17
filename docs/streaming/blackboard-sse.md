# Blackboard SSE subscriptions

The Blackboard API exposes `GET /api/platform/v1/blackboard/events/stream`
with required `scope` and `scope_id` query parameters. The route performs the
same authenticated principal, tenant-bound repository, and resource-scope read
authorization used by Blackboard entry reads.

The subscription tails the existing `blackboard_events` history through
`PostgresBlackboardEventReplay`. Repository writes persist the event and its
platform Outbox record in the same transaction; the SSE route does not create a
parallel event bus or publish side effects. New subscriptions start at the
latest event for the authorized scope. Reconnects send the opaque
`Last-Event-ID` value, which contains the event timestamp and UUID, and replay
all later changes in deterministic cursor order.

Each change is sent as `event: blackboard_update`. The data contains event and
entry identifiers, scope, version, actor, correlation ID, and occurrence time.
Entry content and event payloads are intentionally omitted; clients can fetch
the current entry through the already-authorized Blackboard API. Heartbeats,
no-buffering headers, and disconnect cancellation reuse the shared SSE
transport.

The host must supply `blackboard_event_replay_resolver(principal)`, returning a
`PostgresBlackboardEventReplay` bound to that principal's tenant. The resolver
is checked against the authenticated tenant before a stream is opened. This
poll-based tailer is suitable for the existing durable event source and works
across API processes; production polling cadence and database capacity should
be tuned with deployment telemetry. Real-device and production-host validation
are outside this change.
