# Memory domain boundaries and source references

## Systems of record

| Domain | Owns | Memory may retain |
|---|---|---|
| Knowledge | Canonical documents, chunks, versions, ACLs, retrieval citations | A distilled durable fact and a pinned document/chunk reference; not document text or chunks |
| Blackboard | Expert proposals, transient shared working state, challenges, and decisions | Only the durable outcome of an approved decision and its pinned decision reference; not drafts or working state |
| Session history | Append-only messages, turns, and chronology | A durable fact extracted from a turn and its pinned message/turn reference; not a transcript |

Memory is a governed cross-session projection, not a second document store, collaboration board, or chat archive. The owning domain remains authoritative for content, access policy, retention, and deletion.

## Canonical reference format

Canonical references use the form `DOMAIN://TENANT/RECORD@VERSION`, where DOMAIN is `knowledge`, `blackboard`, or `session`. Tenant and record identifiers are restricted to letters, digits, dots, underscores, and hyphens. Version is mandatory, positive, and has no leading zeroes. Query strings, fragments, user-info, ports, encoded path separators, copied payloads, and memory self-references are rejected. Blackboard references are accepted only for approved decisions.

Examples: `knowledge://tenant-a/doc-17@3`, `blackboard://tenant-a/decision-8@2`, `session://tenant-a/message-91@1`.

The version pin prevents a citation from silently changing when the source record is revised. Consumers must resolve the reference through the owning domain and re-apply that domain's current authorization; possession of a memory record or reference is not an access grant.

## Enforcement boundary

`agent_platform.memory.boundaries` provides the typed reference parser and policy contract. It stores identifiers and versions only. It does not copy canonical payloads, replace source-domain repositories, or bypass their ACL/deletion checks. Existing legacy `source_ref` values remain readable; new integrations should validate and persist canonical references before proposing memory candidates.
