# Alert rules and notification operations (C-16-03)

The alert subsystem is composed from the existing threshold evaluator, durable
PostgreSQL event/outbox store, notification dispatcher, provider adapter and
tenant-scoped acknowledge/resolve API. This task adds deterministic policy
primitives and persists the operational ownership/retry state.

## Thresholds, suppression and aggregation

- `AlertRule` supports finite numeric thresholds and `gte`, `gt`, `lte`,
  `lt` comparisons, severity and cooldown.
- `AlertPolicyEngine` applies inhibition before aggregation. A configured
  source rule suppresses target rules only while the source is firing at or
  above its minimum severity.
- Aggregation groups same-metric/same-severity notifications inside a bounded
  time window and maximum batch size. It returns a digest view; individual
  source events remain durable and independently acknowledgeable.

## Durable delivery and retry

- Rule evaluation writes an idempotent event to the outbox before advancing
  the in-memory cooldown.
- The dispatcher claims rows with `FOR UPDATE SKIP LOCKED`; crashed claims
  can be recovered after the lease timeout.
- Failed delivery uses bounded exponential backoff (5s, 10s, 20s ... capped
  at one hour) and becomes `dead` after the configured attempt ceiling.
- Provider selection remains injected. The webhook adapter requires HTTPS
  and an explicit hostname allowlist; provider response bodies and exception
  messages are not persisted.

## On-call, acknowledgement and resolution

- `OnCallRotation` assigns a deterministic UTC rotation slot and an
  acknowledgement deadline. The evaluation worker persists the assignee and
  deadline with the alert event.
- Authenticated `GET /api/platform/v1/observability/alerts` lists only the
  caller's tenant events, including ownership and delivery state.
- The existing acknowledge/resolve endpoints require alert-manage permission,
  bind actor identity from the authenticated principal and enforce tenant
  scope. An event can be acknowledged once and resolved from open or
  acknowledged state.

## Operations boundary

Provider credentials, endpoint configuration, on-call identities and metrics
snapshots are injected by the host. Automated tests use fakes; real webhook,
server, paging and production-provider exercises are intentionally skipped
for this delivery.
