# Memory deletion propagation

`MemoryService.forget" writes the memory tombstone first and then invokes its
`on_delete(tenant_id, memory_id)" callback. For production use, inject
`PostgresMemoryDeletionQueue.enqueue" as that callback so derived-data cleanup
is durably recorded without copying memory content into the queue.

Apply migration `081_memory_deletion_jobs.sql" before enabling the queue.
Run `MemoryDeletionWorker" in a managed worker process and supply a
host-owned async cleanup callback that removes all derived search-index and cache
entries for the exact tenant/memory pair. The callback must be idempotent because
a worker can lose its lease after cleanup succeeds but before completion is stored.

The queue uses `FOR UPDATE SKIP LOCKED" claims, expiring leases, per-claim
fencing tokens, bounded exponential retry, and a terminal `dead" state. Completion
and failure updates require the current fencing token, so an expired worker cannot
acknowledge a newer worker's lease. Only the exception class is persisted as an
error code; exception messages and memory content are never written to the queue.

The worker is intentionally not auto-started by the API composition root: the
host must provide the actual index/cache deletion handler and manage its lifecycle.
A missing handler therefore cannot be mistaken for successful propagation.
