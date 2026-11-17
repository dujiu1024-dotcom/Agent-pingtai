# Memory layer boundaries

This contract separates short-lived execution context from durable, scope-authorized memory. It is shared by the memory service, extraction pipeline, retrieval layer, and runtime context assembly.

## Layer matrix

| Logical layer | Owner / boundary | Persistence | Promotion rule |
|---|---|---|---|
| Run context | One execution Run within a tenant | Ephemeral execution context and Run snapshots; never a durable `MemoryScope` | Extract a specific fact from an interaction, then submit it as a candidate and require authorized confirmation |
| Session context | One conversation session and its append-only message history | Canonical session/message store; not duplicated as durable memory | Extract a distilled fact with a pinned message/turn reference; never copy a transcript |
| User memory | One user inside one tenant | `MemoryScope.USER` | User-owned facts/preferences; no other user's private facts |
| Agent memory | One named Agent inside one tenant | `MemoryScope.AGENT` | Agent operating knowledge and approved lessons; no user-private facts |
| Project memory | One project and its authorized members inside one tenant | `MemoryScope.PROJECT` | Project facts and decisions; no unrelated project data |
| Organization memory | One tenant / organization | `MemoryScope.TENANT` | Organization-wide facts; never shared across tenants |

The executable mapping lives in `agent_platform.memory.boundaries`:

- `durable_scope_value(layer)` returns the corresponding `MemoryScope.value` for USER, AGENT, PROJECT, and ORGANIZATION.
- RUN and SESSION are explicitly non-durable and raise `ValueError` if a caller attempts to map them to a durable scope.
- `is_durable_memory_layer(layer)` provides a typed predicate; untyped inputs raise `TypeError`.

## Domain-of-record boundaries

| Canonical domain | Owns | Memory may retain |
|---|---|---|
| Knowledge | Documents, chunks, source versions, ACLs, retrieval citations | A distilled fact and a pinned document/chunk reference, never a copied document |
| Blackboard | Transient expert collaboration, proposals, shared working state, decisions | A durable outcome of an approved decision and its versioned reference, never a draft |
| Session | Append-only messages, turns, chronology | A distilled fact extracted from one turn and a pinned message reference, never a transcript |

References are tenant-bound and version-pinned. Blackboard references require an approved decision. Memory does not become the canonical owner of knowledge documents, collaboration state, or session history.

## Lifecycle and authorization invariants

1. Extractors receive a bounded interaction turn, not a full conversation transcript.
2. Extraction creates CANDIDATE records; candidates are not retrievable until an authorized principal confirms them.
3. Durable scope determines visibility and management authority. Project and Agent membership alone does not grant permission to mutate their memory.
4. Run/session context must not be written directly into a durable scope. Promotion requires extraction, source provenance, review, and approval.
5. Tenant identity is part of every durable memory and source reference; cross-tenant promotion or retrieval is prohibited.

## Implementation references

- `agent_platform/memory/boundaries.py`: typed layers, ownership contracts, source-domain policy, and executable durable-scope mapping.
- `agent_platform/memory/extraction.py`: single-turn, bounded, candidate-only extraction.
- `agent_platform/memory/service.py`: scope authorization, candidate confirmation, retrieval visibility, correction and deletion.
- `agent_platform/runtime/run_context.py`: Run-scoped context snapshots and recovery, separate from durable memory.
- `tests/test_memory_boundaries.py`: source reference, layer mapping, and ephemeral/durable separation regression tests.
