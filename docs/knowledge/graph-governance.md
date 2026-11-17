# Knowledge graph governance (C-22-05)

## Lifecycle and version lineage

Graph entities have explicit `active`, `archived`, `expired`, `deleted`, and
`superseded` states. Corrections use optimistic expected-version checks and
append immutable snapshots to entity history. Merge preserves the target identity,
combines source references, marks the source as superseded with `merged_into`,
and increments versions. Only entities of the same type and project scope may
be merged. Attribute conflicts require an explicit `prefer_target` or
`prefer_source` policy.

Source references must be canonical, version-pinned Knowledge, Memory or
Blackboard URIs bound to the same tenant. Corrections do not silently replace
source lineage; merges union source references while preserving target-first order.

## Deletion propagation

Deleting an entity is a soft tombstone for audit/history purposes and atomically:
1. advances the entity version and marks it deleted;
2. tombstones every incident relation and advances relation versions;
3. enqueues idempotent derived-index deletion work for the entity and relations;
4. enqueues entity cache invalidation; and
5. appends an audit event listing affected relation IDs.

Repeated deletion is idempotent. Archived and expired entities may subsequently
be deleted. Active queries must exclude non-active entity/relation states.

Corrections and merges enqueue index upserts and cache invalidations. Archive and
expiry remove the entity from active retrieval without erasing lineage. Invalidation
jobs are coalesced by tenant/object key so a later delete supersedes an earlier
pending upsert and cannot accidentally resurrect stale indexed data.

## Authorization and concurrency

The service derives tenant identity from `KnowledgePrincipal`, never from a
caller-supplied entity payload. Tenant-shared mutations require
`graph.manage` or `knowledge.manage`; project-scoped mutations require the
project in `manage_project_ids` or the tenant-wide `graph.manage` permission.
Cross-tenant access is not discoverable through repository lookup.

Mutations use optimistic version checks. The in-memory reference adapter
serializes each multi-record operation with one async lock, so delete cascades
and merges cannot partially update relations. A persistent adapter must map
each operation to one database transaction and preserve the same compare-and-swap
and audit/outbox invariants.

## Audit and asynchronous invalidation

Audit events are append-only and record tenant, entity, actor, action, source and
target versions, timestamp, and bounded details. Invalidation jobs use leases,
owner-checked completion/retry, bounded retry delays, and dead-letter after
eight failed attempts. Consumers must be idempotent because delivery is
at-least-once.

## Implementation boundary

`InMemoryGraphGovernanceRepository` is a deterministic reference adapter for
contract tests and local workflows. It is not durable production storage. The
PostgreSQL implementation must persist entity/relation version history, audit
events and invalidation outbox in the same transaction; deployment/production
acceptance is gated on that adapter and migration being integrated with the
selected graph storage backend (C-22-02).
