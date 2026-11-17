# Knowledge graph storage and indexing (C-22-02)

## Storage selection

The first graph persistence backend is PostgreSQL using tenant-scoped relational
adjacency lists. This reuses the platform's existing PostgreSQL operations,
backup, transaction and access-control boundary rather than adding a second
stateful service before workload benchmarks justify one.

- `platform_graph_entities`: tenant/entity primary key, type, optional project,
  JSONB properties and a version-pinned canonical source reference.
- `platform_graph_relations`: typed directed edges with same-tenant foreign keys
  to both endpoints. Entity deletion cascades to incident relations.
- `platform_graph_index_jobs`: durable asynchronous indexing outbox, keyed by
  tenant and graph object so repeated writes coalesce to the latest payload.

This is a storage selection for the initial implementation, not a claim that
PostgreSQL is optimal at every graph scale. Neo4j/Neptune/embedded alternatives
remain benchmark candidates if path-query depth or graph size requires them.

## Indexes

The migration adds B-tree indexes for tenant + entity type, tenant + project +
type, normalized title, and the complete source reference tuple. Relations have
outgoing, incoming and project/type indexes. Index-job claim and expired-lease
indexes are partial indexes over pending and processing work respectively.

All entity reads require a tenant predicate. When no project set is supplied,
only tenant-shared rows are visible; when a project set is supplied, tenant-shared
rows plus rows in that authorized set are visible. The caller must derive that set
from the authenticated principal; this repository does not make authorization
decisions. Relations also verify that both persisted endpoints exist, match the
declared endpoint types, and do not bridge project boundaries.

## Incremental writes and async indexing

Entity and relation writes are transactional with outbox enqueue. Repeated writes
to the same tenant/object key update the queued payload and reset a stale lease,
so indexing observes the latest object state. Identity changes (entity source
identity, relation type or endpoints) are rejected instead of silently moving
an existing graph object.

Workers claim pending or expired jobs with `FOR UPDATE SKIP LOCKED`, a bounded
lease, and monotonically increasing attempt count. Completion and retry require
the current lease owner. Retry stores only a bounded error code, schedules a
bounded delay, and dead-letters after the configured attempt limit. Expired
leases that exhaust the retry budget are also dead-lettered during claiming.

The outbox provides durable at-least-once work delivery; index consumers must
remain idempotent. This task defines persistence and queue mechanics, not the
provider-specific full-text/vector index implementation or graph query DSL.

## Verification boundaries

Unit tests use a fake async connection to verify SQL contracts, tenant/project
filters, transactional enqueue shape, endpoint scope checks, lease ownership,
retry exhaustion and migration indexes. They do not replace a live PostgreSQL
integration run. Production deployment must apply migration
`100_knowledge_graph.sql` before enabling the repository.
