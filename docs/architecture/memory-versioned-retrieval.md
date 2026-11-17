# Versioned memory retrieval

MemoryRetrievalService provides deterministic retrieval over immutable memory versions. It is separate from candidate extraction/review and does not auto-confirm candidates.

## Request contract

- MemoryRetrievalQuery supports an as_of timestamp, created_after/created_before bounds, scope filters, source-reference filters, minimum confidence, text matching, a recall strategy, and a bounded result limit.
- All timestamps must be timezone-aware. Historical retrieval selects the newest version recorded at or before as_of; it does not substitute the current version.
- Only ACTIVE versions valid at the requested time are eligible. Expired, archived, superseded, deleted, and unreviewed candidate records are excluded.
- Tenant, user, project, and Agent visibility is evaluated before results are returned. Project/Agent membership grants read visibility; management grants do not widen visibility beyond the authenticated tenant. Tenant-shared memory requires memory.manage.
- Repositories must implement list_versions. Unsupported adapters fail closed rather than silently returning only the current version.

## Recall and citations

- RECENCY: newest recorded versions first.
- CONFIDENCE: highest confidence first; confidence is ranking metadata, never an authorization substitute.
- RELEVANCE: deterministic lexical term overlap weighted with confidence and recency. This is a baseline strategy; vector/semantic recall can be added behind the same contract.
- Every hit carries a citation containing memory ID, exact version, source reference, scope/scope ID, and recording timestamp.

## Validation

Unit tests cover historical correction lookup, source/version citations, scope and tenant isolation, expiry at a requested time, filters, ranking, and invalid time bounds. PostgreSQL version history is persisted by PostgresMemoryRepository and platform_memory_versions (migration 009_memory_versions.sql).
