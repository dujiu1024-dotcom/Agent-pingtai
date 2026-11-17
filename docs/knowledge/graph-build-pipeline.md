# Graph build pipeline (C-22-03)

## Flow
1. A trusted source adapter supplies a bounded, versioned source reference and text for a Knowledge document, Memory record, Blackboard event, or File asset.
2. A replaceable `GraphExtractor` emits typed entity/relation proposals with confidence and optional source offsets. The included rule-based extractor is a deterministic fallback; model-backed extraction is injected behind the same contract.
3. The pipeline validates tenant/project scope, schema types and properties, confidence, endpoints, offsets and batch limits before writing a candidate.
4. Candidate IDs are deterministic per tenant/source kind/source ID/source version. Repeated delivery of the same source revision returns the original candidate.
5. Candidates remain pending until an authorized reviewer approves/rejects them. Conflict candidates require explicit selection; rejected alternatives remain stored for audit.
6. Approval writes graph entities and relations with deterministic IDs and canonical source references. Writes are idempotent so a retry after partial failure can safely resume.

## Source mapping
- Knowledge document → `GraphSourceDomain.KNOWLEDGE/document`
- Memory record → `GraphSourceDomain.MEMORY/memory`
- Blackboard event → `GraphSourceDomain.BLACKBOARD/entry`
- File asset → `GraphSourceDomain.KNOWLEDGE/file_asset` → graph `document`

The graph stores references and extracted metadata only; canonical source content remains in its owning domain. Source adapters must enforce the source system's own ACL before constructing `GraphSource`.

## Conflict and review policy
Candidates are scoped by tenant and project. Exact normalized entity keys are used for deduplication. Conflicting or ambiguous proposals are not auto-published; reviewers need `project_ids` membership or `graph.manage`. Rejections and review metadata are retained. A conflict resolution explicitly selects one candidate and preserves the other candidate as rejected.

## Integration boundary
`GraphCandidateRepository` is the candidate-workflow port and `GraphWriteRepository` is the graph storage port. `MemoryServiceGraphCandidateRepository` persists review state through the existing MemoryCandidate/MemoryRepository lifecycle; it deliberately stores extracted proposal JSON and source references, not raw source text. `InMemoryGraphCandidateRepository` is for tests/development only. Production wiring must inject a durable MemoryRepository, a tenant-scoped graph repository and a trusted extractor. No external model, server, or production resource is provisioned by this change.
