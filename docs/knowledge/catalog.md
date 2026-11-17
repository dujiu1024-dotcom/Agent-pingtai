# Knowledge catalog

The collection/index catalog is separate from document content storage. It uses
`knowledge_collections`, `knowledge_collection_documents` and `knowledge_indexes`
(migration `067_knowledge_catalog.sql`) and keeps tenant identity in every
primary key and lookup predicate.

## Production wiring

Construct `PostgresKnowledgeCatalog` with the application's async PostgreSQL
connection factory and pass it as `knowledge_catalog` to `create_platform_app`.
The app resolves `KnowledgePrincipal` from the authenticated identity repository;
clients cannot supply tenant or user identity in request bodies.

## Scope rules

- A shared tenant collection accepts only live tenant-scoped documents.
- A project collection accepts only live project-scoped documents from that exact project.
- Private documents cannot be attached to collections.
- Collection reads are tenant-filtered and project-filtered before serialization.
- Index creation is bound to a live collection in the same tenant. Duplicate
  collection/index identities are idempotently rejected.
- Index dimensions and schema versions must be positive integers; booleans are
  not accepted as integers.

`PostgresKnowledgeCatalog` exposes collection creation, lookup, listing,
document membership, index creation and index listing. Document versions,
chunks, embeddings and ACL records remain owned by `PostgresKnowledgeRepository`.