# Knowledge Graph Extraction Pipeline (C-22-03)

The host resolves canonical Knowledge, Memory, Blackboard or parsed File records
and enforces their source ACL before constructing `GraphSourceRecord`. File
assets should enter through the Knowledge parser and be represented as a
versioned Knowledge document; raw file bytes are never passed to the extractor.

The injected extractor receives bounded source text and the tenant's
`GraphSchema`, then returns typed `GraphExtractionDraft` objects. The pipeline
validates tenant/project equality, source-domain mappings, schema properties,
relation contracts, candidate count and confidence. Source text is not copied
into candidate metadata or graph attributes by default.

All output is review-gated. Low-confidence output is marked
`review_required`; conflicting output is marked `conflict`. Nothing is written
to the graph before an authorized reviewer confirms a candidate or resolves a
conflict. Conflict resolution supersedes the previous candidate and removes its
old graph projection. Candidate IDs are deterministic per source revision and
entity ID, making retries idempotent.

Tenant identity is taken from `GraphPrincipal`. Project source records must be
within the caller's project allow-list. Review requires project management
permission or `graph.manage`; tenant-wide records require `graph.manage`.
The host remains responsible for resolving the canonical source and applying
its source ACL before invoking the pipeline.

The extractor and graph repository are injected. This module does not select a
model provider or send data to an external service. The in-memory candidate
repository is for tests/development only; production must provide a durable,
tenant-scoped candidate store with optimistic version checks and audit
integration. The PostgreSQL graph repository from C-22-02 implements the
entity/relation upsert contract. Production composition-root wiring, scheduled
ingestion, connector callbacks and deletion propagation remain separate work.
