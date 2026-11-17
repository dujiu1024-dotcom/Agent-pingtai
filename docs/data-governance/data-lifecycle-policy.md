# Data lifecycle governance

Scope: Issue #19 / C-19-04 only.

## Retention and archive
A tenant-scoped policy defines a positive retention period, an optional archive age
(no later than expiry), and a legal hold. A legal hold suppresses both archive and
deletion. The scheduler supplies tenant/resource scope and bounded page size; expired
records are archived first when configured, then receive a deletion request at expiry.
The source adapter owns the authoritative created/updated timestamp query.

## Export
An authorized actor requests an export for one tenant and resource type. The service
records an export request and exposes an async JSONL stream. Tombstoned resources are
filtered at read time so delayed physical cleanup cannot reintroduce deleted records.
The host is responsible for storing the stream in its approved export destination,
expiry, download authorization and encryption at rest.

## Deletion and tombstones
Deletion requires `data.delete`; policy changes and retention execution require
`lifecycle.manage`; exports require `data.export`. The tombstone is durable and
tenant/resource keyed. Creating it and its propagation outbox rows is one PostgreSQL
transaction. Repeated requests return the existing tombstone and do not duplicate
outbox work.

## Cross-system propagation
Each tombstone fans out durable jobs for the search index, cache and object storage.
The worker claims one sink job with a lease, calls the matching idempotent sink adapter,
then marks that sink complete. Expired leases can be reclaimed. The deletion request
is marked propagated only after every sink succeeds. A failed sink remains retryable
after its lease expires; it must not be treated as fully deleted. Read paths and export
must continue to honor tombstones while physical propagation is pending.

## Integration boundary
The PostgreSQL ledger and worker contracts are implemented here. Host composition
must supply the real source, archive, search-index, cache and object-storage adapters
and schedule the retention/propagation workers. This change does not claim that every
existing production store has already been wired to these adapters. Real infrastructure
and machine verification are intentionally outside this PR.
