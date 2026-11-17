# Legacy `vectors.py` reuse and migration policy

## Decision

`vectors.py` is a legacy, standalone retrieval implementation. It is **not** the
platform Knowledge Base repository, ingestion pipeline, or RAG runtime. Keep it
available for existing CLI/workflow consumers during the migration window, but
freeze its feature surface: new platform Knowledge/RAG features must not import
it, write to its `db.py` / local SQLite `embeddings` table, or treat its search
results as authoritative platform knowledge.

The platform path is the `agent_platform.knowledge` domain:
`KnowledgeService` + `KnowledgeRepository` (production
`PostgresKnowledgeRepository`) + versioned embedding profiles + ACL-aware
retrieval/RAG services. PostgreSQL/pgvector is only the vector storage/search
primitive; it does not by itself provide ingestion, document lifecycle,
provenance, tenant/project authorization, deletion propagation, or answer
citations.

## Reuse boundary

- Reuse concepts only where useful: chunking heuristics, operational lessons,
  and provider behavior may inform platform adapters after review.
- Do not share the legacy persistence schema, `db.py` connection lifecycle,
  `embeddings` rows, hash bucket identifiers, or in-memory matrix cache with
  the platform repository.
- Do not expose the legacy `index_path`, `index_text`, `search`, or
  `delete_by_source` functions as platform Knowledge APIs.
- Keep legacy callers on the legacy module until they are explicitly migrated;
  do not silently redirect their writes to PostgreSQL or combine result sets.

## Content migration procedure

Migration is **content-level re-ingestion**, not vector-row copying:

1. Inventory each legacy source and its owner/project, source type/ID, source
   URI, content hash, and permitted audience. Quarantine ambiguous ownership or
   access scope; do not promote it to shared/tenant knowledge by default.
2. Export the original source content and provenance. Do not use legacy vector
   payloads as the source of truth. Legacy dense vectors may have provider/model
   dimensions and normalization that differ from the platform profile; hash-TF
   buckets are not portable semantic embeddings.
3. Submit approved content through `KnowledgeService.ingest` (or the
   authenticated upload/connector path), allowing the platform parser/chunker
   to create document versions and chunks with source locations.
4. Generate embeddings through the configured `VersionedEmbedder` profile and
   persist through `KnowledgeRepository.save_embeddings`. Record model,
   version, dimensions, and content hash; let the platform index/search strategy
   own hybrid retrieval.
5. Reapply and verify tenant/project/document/chunk ACLs before enabling
   retrieval. Validate representative queries, citations/source locations,
   deletion propagation, and no cross-tenant/project leakage.
6. Cut over one source at a time only after validation. Keep the legacy source
   read-only during the observation/rollback window; after consumer inventory
   confirms no remaining callers, archive the legacy index and remove its
   dependency in a separate, explicitly tracked change.

No automatic bulk migration or dual-write is enabled by this policy. A migration
must be auditable and idempotent at the document/source version level.

## Exit criteria

The legacy module can be retired only when all known consumers are inventoried
and migrated, approved source content is present in the platform repository,
ACL/provenance/deletion checks pass, and no production caller imports the
legacy module. A successful pgvector query alone is not a retirement or
Knowledge Base completion criterion.
