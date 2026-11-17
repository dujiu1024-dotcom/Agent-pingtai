# Graph extraction pipeline and candidate review

C-22-03 adds a bounded, candidate-first extraction boundary for canonical Knowledge documents, Memory records and Blackboard entries. The caller supplies a single already-authorized source record with tenant, source type/id/version, canonical source reference, optional project and bounded text. The extraction adapter does not enumerate source repositories or receive a whole corpus.

## Extraction contract

A host injects a `GraphExtractor` and tenant `GraphSchema` provider into `GraphExtractionService`. Extractors return entity drafts and relation drafts with local IDs, schema-validated attributes, confidence in [0, 1], evidence references and optional conflict keys. Relation endpoints must refer to entities in the same extraction batch and satisfy the schema's directed source/target type constraints.

The service rejects unmapped source domain/type pairs, cross-tenant schemas, oversized source text/batches/payloads, duplicate local IDs, invalid confidence, missing canonical evidence references, unknown relation endpoints and schema-invalid properties before persisting anything. Candidate IDs are deterministic for a source version and canonical draft, making retries idempotent.

## Review workflow

Candidates are persisted in `platform_graph_extraction_candidates` by `PostgresGraphCandidateStore`. Candidates remain non-queryable proposals; this pipeline does not write directly into graph entity/relation tables. Conflicting proposals sharing a conflict key within a source batch are marked `conflict` for human review. Review transitions require a reviewer identity and reason and allow only candidate/conflict -> accepted/rejected; the SQL update is tenant-scoped and conditional to prevent replaying a completed review.

Accepted-candidate materialization is a separate trusted operation: it must re-check current source ACL, schema version and endpoint state before writing to `PostgresGraphRepository`. The candidate review endpoint does not itself grant project access or bypass canonical source authorization.

## Provenance and boundaries

Every candidate retains tenant, source domain/type/id/version, source reference, optional project, confidence, evidence references and extraction payload. The source version is part of the deterministic identity, so a new canonical source version can produce a new proposal without overwriting prior evidence.

The host must authorize the source before invoking extraction and must supply a tenant-specific schema. The injected extractor/provider is responsible for model credentials, timeout/rate limiting and model-specific safety. No production model, automatic source polling, cross-source entity resolution, or accepted-candidate materializer is configured by this module.
