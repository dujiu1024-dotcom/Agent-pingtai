# Version-aware memory conflict resolution

## Contract

MemoryConflictResolver is the governed entry point for resolving a conflict set created by MemoryService.propose. It supports two explicit decisions:

- select: keep one existing memory and mark competing active/conflict records SUPERSEDED.
- merge: create a new memory identity containing caller-reviewed merged content, activate it, and supersede every competing record.

The resolver never silently chooses by confidence or timestamp. The caller must identify a selected source memory; merge additionally requires non-empty reviewed content.

## Provenance and audit

Each decision captures tenant, conflict key, scope, actor, timestamp, resolution kind, selected source, resulting memory/version, and exact source memory IDs, versions, and source references. A merged memory stores `conflict_decision_id`, `merged_from`, and the complete `merged_source_refs` list in metadata. Its `source_ref` remains the selected source's canonical, version-pinned pointer; the decision audit records every contributing source/version. Synthetic conflict URIs are not used because they do not resolve to a record owned by Knowledge, Blackboard, or Session.

Audit append is write-ahead: if the audit repository is unavailable, no memory transition is attempted. The included in-memory audit repository is for tests/local development; production must inject a durable append-only implementation and coordinate decision persistence and memory mutation transactionally.

Audit history is tenant- and scope-management-authorized. The resolver rejects cross-tenant access, unauthorized scope access, non-member selections, undersized conflict sets, and malformed merge requests.

## API

```python
resolver = MemoryConflictResolver(memory=memory_service, audit=audit_repository)
record = await resolver.resolve(
    principal=principal,
    conflict_key="profile.preference",
    selected_memory_id=source_memory_id,
    resolution=MemoryConflictResolution.MERGE,
    merged_content="reviewed consolidated preference",
)
decisions = await resolver.history(principal=principal, memory_id=record.candidate.memory_id)
```

This module does not connect to a production audit database or external service. Such an adapter is deployment-specific and remains outside this code-only change.
