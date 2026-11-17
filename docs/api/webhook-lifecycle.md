# Webhook lifecycle contract

The platform webhook subsystem exposes tenant-scoped subscription administration under `/api/platform/v1/webhooks`.

## Subscription lifecycle
- Create: HTTPS public destination only; reject localhost/private/reserved targets; require at least 32 bytes of signing secret; persist only a secret-manager reference.
- List: tenant-scoped; never return signing secret references or secret material.
- Rotate: store replacement secret first, atomically switch the subscription reference, then revoke the previous secret; revoke the replacement if the switch fails.
- Disable: disable the subscription and revoke its secret. Disabled subscriptions are excluded from dispatch and dead-letter replay.
- All create/rotate/disable API operations require a write-ahead audit record. Missing audit configuration returns 503 before secret or subscription mutation.

## Delivery protocol
Events are JSON envelopes with stable event ID, topic, tenant and data. Requests carry event ID and an HMAC-SHA256 signature over timestamp + body. Consumers should verify the timestamp within a bounded tolerance, compare signatures in constant time, and deduplicate by event ID. Delivery uses pinned public DNS addresses to prevent SSRF/rebinding; TLS must still validate the original hostname.

## Retry and dead letters
The dispatcher claims durable delivery rows, records a system-actor audit intent before external I/O, applies bounded exponential retry, and moves exhausted deliveries to dead-letter state. A delivery ID/event ID is the deduplication anchor; a retry must not create a new event identity.

## Replay and audit
Dead-letter listing and replay are tenant scoped and require webhook-management authorization. Replay additionally requires an explicitly configured replay service and a non-empty operator reason. The replay store only requeues a dead letter when its subscription remains active and still subscribes to the event topic. Replay is audited with the human actor; asynchronous delivery attempts are audited as `system:webhook-dispatcher`.

## Operational guarantees
- Secret material is never returned by subscription APIs.
- Missing audit or replay services fail closed.
- Production uses durable PostgreSQL delivery/lease state and a shared secret manager.
- Delivery endpoints must be reachable over public HTTPS; do not allow redirect-based bypass of destination validation.
- Every claim carries a UUID lease token. Completion, retry, and dead-letter transitions are conditional on the same token and leased status; an expired worker cannot overwrite a row reclaimed by another worker.
- Retry/dead-letter rows persist only the HTTP response code and a bounded error-type code, never raw exception text or request credentials.
- Subscription audit details record the destination hostname and topics, not URL paths/query strings or signing-secret material; denied management requests are audited before returning 403.
