# Prompt version and variant lifecycle

Prompt versions remain immutable records identified by `(prompt_id, version)` and a SHA-256 content hash. A named variant is a mutable pointer to one immutable version, allowing independent tracks such as `prod`, `candidate`, `ab-a`, and `ab-b`.

## Lifecycle

- Create a variant by binding a valid existing prompt version to a unique lowercase variant name.
- Assign a different existing version to an active variant to advance or roll it back. Every assignment is appended to `platform_prompt_variant_versions`; immutable prompt versions are never rewritten.
- Deprecate a variant to prevent future assignments. A variant whose current version is the active or canary deployment target cannot be deprecated.
- Read current variants and their assignment history through the registry adapter.
- The PostgreSQL adapter appends every create, assignment, deprecation, activation and rollback event in the same database transaction. The in-memory registry exposes the same lifecycle evidence through immutable `PromptAuditEvent` records and `list_audit`.
- `PromptRegistry.rollback` and `PostgresPromptRegistry.rollback` restore a previously registered immutable version and record the actor and selected version. Rollback never rewrites a version.

## Persistence and isolation

Migration `083_prompt_variants.sql` adds tenant-key-compatible variant identity, foreign keys to immutable prompt versions, assignment history, and an index for prompt/status lookup. The existing Prompt Registry API derives its storage key from the authenticated tenant; callers must not accept tenant IDs from request bodies.

This change supplies the domain and persistence contract for C-23-01. Version-management HTTP endpoints and workbench integration remain C-23-04; A/B execution and traffic routing remain C-23-02/C-23-03.
