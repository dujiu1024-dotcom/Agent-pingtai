# Memory version-aware retrieval

## Retrieval contract

`MemoryRetrievalService` is the policy-aware read interface. It returns current ACTIVE memories only, filtered by the authenticated principal's tenant and scope visibility. Candidate, conflict, superseded, archived, expired and deleted records are never returned by ordinary recall.

Each result preserves the memory version, source reference, scope, confidence, creation/update timestamps and applicability interval. Consumers should retain the source reference when rendering or grounding an answer.

## Temporal applicability

- `applies_from` is inclusive: a memory is not applicable before this instant.
- `applies_until` is exclusive: a memory is not applicable at or after this instant.
- Both are timezone-aware UTC instants; a null bound is open-ended.
- `expires_at` remains a lifecycle expiry and independently suppresses retrieval.
- Optional created-after/before filters constrain the record creation timestamp.

## Scope and permissions

The authenticated principal is authoritative. Tenant isolation and USER/AGENT/PROJECT/TENANT visibility are inherited from `MemoryService`; callers cannot broaden access by supplying scopes. Scope filters only narrow the visible result set. Historical version reads require the same scope-management permission used by memory history, or current visibility for an otherwise readable active memory.

## Versions and provenance

Ordinary recall returns the current version only. Historical reads require an explicit memory ID and version; only snapshots whose status is ACTIVE and whose applicability window includes the requested instant can be recalled. This prevents candidates, tombstones and superseded/archived versions from becoming active through a version selector. Every hit carries the original source reference and exact version.

## Recall strategies

- `RELEVANCE`: lexical term match blended with confidence and recency; when query text is supplied, non-matches are excluded.
- `CONFIDENCE`: rank by extractor/reviewer confidence.
- `RECENCY`: rank by last update time.
- Confidence thresholds, source-reference prefixes, scope subsets, time bounds and result limits are applied before ranking.

This is a deterministic baseline ranker, not semantic/vector search. A vector or hybrid retriever can be added behind the same result contract without changing authorization or temporal policy.
