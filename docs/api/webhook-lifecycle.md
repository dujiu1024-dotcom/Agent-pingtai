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


## 禁用与密钥撤销恢复

禁用订阅先在数据库中将状态切换为 disabled，再撤销 Secret Manager 中的签名密钥。两者无法组成同一事务；若外部撤销暂时失败，订阅仍保持禁用，后续重复调用禁用接口必须继续返回待撤销的 secret reference 并重试。Secret Manager 的 revoke 操作必须幂等；不得因第一次撤销失败而丢失待清理引用。


## 密钥轮换撤销恢复

轮换签名密钥时，必须在同一数据库事务中切换订阅当前 secret reference，并将旧 reference 写入 `platform_webhook_secret_revocations` 待处理表。外部 Secret Manager 撤销成功后才删除待处理记录；撤销失败时记录保留，由后台任务调用 `retry_pending_secret_revocations` 重试。禁用订阅同样先持久化待撤销引用，再执行外部撤销。Secret Manager 的 revoke 必须幂等，多个 Worker 并发重试不得影响订阅状态。


## Secret revocation worker leases

Pending secret revocations are claimed in a single PostgreSQL transaction using `FOR UPDATE SKIP LOCKED`. Each claim rotates a UUID fencing token and sets a bounded lease expiry. A worker may delete a queue row only when its token still matches and the lease has not expired. Expired work becomes claimable by another worker; a stale worker cannot acknowledge a reclaimed row. Secret Manager revocation remains idempotent because an external revoke may succeed immediately before a worker loses its lease.
