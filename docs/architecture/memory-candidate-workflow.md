# Memory candidate extraction and review workflow

## Candidate lifecycle

1. An extractor receives a bounded interaction turn, not a full conversation transcript.
2. Every draft is persisted as a candidate with its source reference and a finite confidence score in [0, 1].
3. Candidates are not retrievable until a reviewer confirms them.
4. Candidate review must go through `MemoryCandidateWorkflow`: USER memory is reviewable by its owner or a memory manager; PROJECT and AGENT memory require the corresponding scoped management grant (or `memory.manage`); TENANT memory requires `memory.manage`.
5. A review event records tenant, memory, decision, reviewer, source reference, candidate version, note, and timestamp. Production deployments must inject a durable append-only audit repository; the in-memory adapter is for tests/local use only.
6. A CONFLICT candidate cannot be confirmed/rejected as an ordinary candidate. A reviewer must explicitly choose a winner through `MemoryService.resolve_conflict`; other records become SUPERSEDED and trigger derived-index/cache cleanup.
7. Corrections create a new memory version. Rejection/forgetting use a deletion tombstone and retry derived-data cleanup. Expiry transitions ACTIVE to EXPIRED; archive transitions ACTIVE to ARCHIVED. Both are excluded from retrieval and retry cleanup through the configured lifecycle hook.

## Integration contract

- Use `MemoryExtractionService.propose_from_turn` to create candidates.
- Use `MemoryCandidateWorkflow.review` for candidate confirmation or rejection and review history.
- Do not expose the low-level `MemoryService.confirm` / `reject` methods directly to untrusted application callers; they are repository/service primitives and do not establish the caller's scope authorization.
- Do not auto-confirm based on confidence. Confidence is ranking/evidence metadata, not an authorization or consent substitute.
- Source references must remain traceable to the originating interaction or document. Do not persist whole chat transcripts as long-term memory.
- Conflict resolution, correction, forgetting, expiry and archive remain explicit lifecycle operations; derived index/cache cleanup must be idempotent and retryable.

## Scope

This change adds the authorized review boundary and review audit contract only. It does not implement memory retrieval versioning, knowledge-base/Blackboard integration, or user/tenant deletion export APIs.
