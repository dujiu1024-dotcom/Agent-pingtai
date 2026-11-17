# Tenant-scoped graph storage

C-22-02 adds a PostgreSQL graph record store. The graph is a derived, indexed projection: Knowledge documents, Memory records and Blackboard entries remain canonical in their owning domains. Graph records carry source references/metadata and attributes, not copied canonical bodies.

## Storage contract

Apply `agent_platform/storage/postgres/090_graph_storage.sql` after platform core. Entity and relation primary keys include tenant_id. Relations have composite foreign keys to both endpoint entities, cascading when an endpoint is deleted. Indexes support tenant/type lookup, optional project/type filtering, canonical source lookup, and outgoing/incoming adjacency traversal.

## Repository

Construct `PostgresGraphRepository(tenant_id=..., schema=..., connection_factory=...)` or use `from_dsn` with psycopg. Every read/write is bound to the repository tenant. Entity writes validate attributes against the supplied tenant schema and cannot change an existing entity ID to a different type. Relation writes validate the declared endpoint types and verify the persisted endpoints have those types. `list_relations` returns bounded incident edges.

The supplied GraphSchema remains the validation authority for this repository instance. Runtime schema extension persistence/version coordination is separate from this record-store slice; callers must load the correct tenant schema before constructing the repository.

## Authorization boundary

Tenant scoping is enforced in SQL, but project membership and source-object ACL checks still belong to the trusted host/service layer before calling the repository. A tenant match alone is not proof of project access. The graph is not a replacement for Knowledge, Memory or Blackboard records.
