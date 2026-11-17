# Knowledge Graph Schema (C-22-01)

## Purpose and ownership

The graph schema is tenant-owned metadata, not a graph database implementation.
It defines entity/relation vocabulary, typed property constraints and source-domain
mappings. Graph persistence, indexing, extraction, query execution and visualization
are separate work items.

## Built-in source mappings

| Canonical domain | Source kind | Graph entity |
|---|---|---|
| Knowledge (#10) | document | document |
| Memory (#11) | memory | memory |
| Blackboard (#13) | entry | blackboard_entry |

The graph stores references to canonical records; it does not replace their source
of truth. A production adapter must preserve the source tenant/project ACL and
resolve the referenced record before exposing content.

## Schema rules

- Entity and relation names are lowercase identifiers with a bounded length.
- Properties declare a type, required/indexed flags and optional length, numeric
  range or enumerated values.
- Boolean values are not accepted as integers/numbers.
- Relations declare allowed source and target entity types.
- All graph records must match the schema tenant. Project IDs are carried as
  explicit scope metadata; callers still enforce project membership and ACL.
- Runtime extensions are registered per tenant. Built-in types and duplicate
  definitions cannot be overwritten. Registration is in-process only in this
  task; durable storage and distributed concurrency belong to C-22-02.

## API sketch

`GraphSchemaRegistry.get(tenant_id)` loads the tenant schema (or platform defaults).
`register_entity_type` and `register_relation_type` add validated tenant-owned
extensions. `GraphEntity.validate(schema)` and `GraphRelation.validate(schema)`
check property and endpoint contracts before persistence.

## Security boundary

This module validates schema shape and tenant equality; it is not an authorization
engine. Production graph repositories must derive tenant/project scope from the
authenticated principal, resolve source references against their canonical stores,
and enforce ACLs on every read/write. Do not trust tenant/project IDs supplied by
model output.
