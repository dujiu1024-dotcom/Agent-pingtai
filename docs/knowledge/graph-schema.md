# Knowledge graph schema contract (C-22-01)

This module defines the typed graph vocabulary and source-domain mapping. It is a
schema contract only: it does not select a graph database, persist graph records,
consume Blackboard events, or perform extraction.

## Built-in entity types

| Entity type | Canonical source | Required / bounded properties |
|---|---|---|
| `KnowledgeDocument` | `knowledge://tenant/record@version` | canonical_id, title; optional approved/active/archived status |
| `KnowledgeChunk` | `knowledge://tenant/record@version` | canonical_id, text (20,000 chars max), ordinal |
| `MemoryItem` | `memory://tenant/record@version` | canonical_id, title, scope (user/agent/project/tenant), optional confidence |
| `BlackboardDecision` | `blackboard://tenant/record@version` | canonical_id, title, status; source must be approved |
| `FileAsset` | `file://tenant/record@version` | canonical_id, title, optional media_type |

Every source reference pins a positive version and a tenant. Cross-tenant references,
non-canonical URIs, unknown schemes, query/fragment components and unapproved
Blackboard decisions are rejected. Graph records retain references and metadata,
not source document, transcript, or file bytes.

## Built-in relation types

- `CONTAINS`: KnowledgeDocument -> KnowledgeChunk.
- `DERIVED_FROM`: MemoryItem / BlackboardDecision / KnowledgeDocument / KnowledgeChunk
  -> KnowledgeDocument / KnowledgeChunk / FileAsset; optional numeric confidence.
- `REFERENCES`: BlackboardDecision -> KnowledgeDocument / KnowledgeChunk /
  MemoryItem / FileAsset.

Relations are directed by default and validate both endpoint types and typed
attributes. Storage adapters may map these contracts to SQL, a graph database, or
an embedded implementation without changing domain semantics.

## Runtime extension

`default_graph_schema()` returns the built-in immutable registry.
`register_entity_type()` and `register_relation_type()` return new registries;
they do not mutate the original. Duplicate identifiers, unknown relation endpoints,
malformed definitions, and attempts to mutate registry mappings fail closed.
Extensions are in-process schema declarations only; a future persistence layer
must add authorization, audit, migration and schema-version controls before
accepting tenant-provided definitions.

## Domain boundaries

- Knowledge owns documents, chunks, versions, ACLs and source citations.
- Memory contributes approved durable items only; transient candidates are not
  graph entities.
- Blackboard contributes approved decisions only; proposals and unreviewed
  messages remain outside the graph.
- File storage owns bytes, scanning and download authorization; the graph stores
  only versioned references and bounded metadata.

Tenant authorization and source existence must still be verified by the future
ingestion/storage adapter at read/write time. A syntactically valid reference is
not proof that the caller is authorized to access it.
