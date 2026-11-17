# PostgreSQL graph storage and indexes (C-22-02)

## Backend choice

PostgreSQL is the initial graph backend. Nodes and directed edges are stored in
`graph_entities` and `graph_relations`; relation rows are the adjacency list.
This avoids adding an operational graph database before workload and deployment
requirements justify one. The repository API is isolated so a later Neo4j,
Neptune, or embedded adapter can implement the same contract.

## Tenant and project boundaries

Every primary key and lookup is tenant-scoped. Relation endpoint foreign keys
include tenant_id, so an edge cannot connect nodes across tenants. Every
project-scoped read/write accepts the project IDs already authorized by the host;
tenant-wide records (`project_id IS NULL`) remain visible within that tenant.
This repository is not an authorization service: callers must derive tenant and
project allow-lists from the authenticated principal, never model output.

## Index strategy

- B-tree: tenant + entity type + normalized entity name (prefix lookup).
- B-tree: tenant + project + entity type.
- Partial unique B-tree: tenant + canonical source domain/type/id/version, preventing
  duplicate graph nodes for one source revision.
- GIN `jsonb_path_ops`: entity and relation attributes.
- B-tree: tenant + source/target entity + relation type + creation time.

PostgreSQL maintains these indexes transactionally with each write; no separate
asynchronous indexer can lag the canonical tables. Source-version changes are
upserts with a new source reference version; older revisions remain addressable.

## Write and deletion semantics

`PostgresGraphRepository.upsert_entity` validates the tenant schema and source
mapping before an idempotent tenant/entity-key upsert. `upsert_relation` locks
both endpoint rows for key-share, verifies stored endpoint types and project
scope, then writes the edge in the same transaction. Deleting a node cascades to
its incident edges. Canonical Knowledge/Memory/Blackboard records remain the
source of truth; this graph store keeps references, not copies of source payloads.

## Operational boundary

Apply migration `082_graph_storage.sql` before constructing the repository.
The adapter uses psycopg 3 async connections. Production connection pooling,
authenticated principal resolution, project ACL decisions, source-record
existence checks and any future external graph service remain host responsibilities.
