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


## Retrieval test and citation drill-through contract (UI-G2-760)

`GET /api/platform/v1/knowledge/search?q=...` returns the backward-compatible
`items` array plus query context: `query`, normalized filters, `strategy`,
`top_k`, `elapsed_ms` and `result_count`. Each item includes a 1-based
`rank`, immutable source locator (document/version/chunk/page/offset), score,
retrieval trace, and a `citation_path` suitable for navigation to the source
document's chunk preview. The citation path is a UI route, not an authorization
token; the destination must fetch through the normal tenant/ACL-protected API.
Search filters are applied by the knowledge service after the repository's
tenant/resource ACL-scoped candidate query. Query text is returned only in the
interactive search response and must not be copied into telemetry or audit logs.

The current ingestion APIs execute parsing/chunking/embedding synchronously and
return a completed result. They do not expose a durable asynchronous ingest-job
resource, progress stream, retry endpoint, or failed-job state machine. UI must
show the synchronous request lifecycle and errors rather than fabricate
progress states. Durable queued ingest/retry remains a backend capability gap.
