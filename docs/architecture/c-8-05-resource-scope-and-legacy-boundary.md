# C-8-05 resource scope, audit, and legacy migration boundary

## Scope contract

- Every session, message, file asset, and connector is tenant-owned. PostgreSQL repositories remain tenant-bound and every lookup/write includes the tenant key.
- A non-null `project_id` narrows a resource to exactly one project in that tenant. Project-scoped reads and writes require the corresponding project authorization in addition to the resource authorization.
- A null `project_id` means tenant scope, not "unknown project" and not membership in every project. Do not silently infer a project from an owner, filename, path, legacy table, or current UI selection.
- A file linked to a session inherits that session's project. A caller-supplied project that differs from the session project is rejected. Messages can reference only assets in the same project/session scope.
- Connector credentials remain opaque `secret://` references. Project binding is metadata only; it never copies credentials into audit details or sync payloads.

## Audit contract

- Use the existing append-only `PostgresAuditWriter` / `AuditWriter.record` contract; do not create a second audit ledger.
- Resource APIs emit resource actions with tenant, authenticated actor, resource identifier, project scope, and available trace/correlation identifiers.
- Audit detail is metadata only. Never include message bodies, file bytes, access tokens, secret references, OAuth codes, or connector page contents.
- Existing HTTP request auditing remains the outer request-level record. Resource events complement it; they do not replace it.

## Legacy adapter and migration boundary

- `agent_platform/control_plane/legacy_conversation_adapter.py` is a pure, read-only mapper: it requires explicit tenant, actor, and approved project mappings; it produces deterministic IDs and provenance metadata but has no persistence capability.
- `conversation.py` and historical docs tables are legacy compatibility sources only. They are not authoritative stores for new sessions, messages, or file assets.
- New writes go only through `SessionAssetService` and tenant-bound PostgreSQL repositories. No dual-write to legacy stores is permitted.
- Any future import must be an explicit, idempotent, separately reviewed migration: preserve source identifiers as provenance, require an explicit tenant mapping, resolve project mapping from an approved mapping table, quarantine ambiguous/orphaned rows, and emit per-row audit evidence.
- Migration `048_resource_project_scope.sql` adds connector project binding and indexes. Existing connector/session/file rows are left unchanged; null project values remain tenant-scoped until explicitly mapped. No automatic project inference or bulk legacy import is performed by C-8-05.
- A future read-only legacy adapter may expose legacy records for migration preview/export, but must not satisfy new-platform repository interfaces or accept writes.

## Operational boundary

No live server, production database, external OAuth provider, or real object store is provisioned by this change. CI PostgreSQL/Redis integration tests are the automated evidence; real-device validation is intentionally skipped.


## A-8-03 acceptance clarification: legacy conversation and docs tables

- `conversation.py` plus SQLite `conversations`/`config_kv` are compatibility inputs only. `LegacyConversationAdapter` maps approved source rows into canonical `ConversationSession` / `ConversationMessage` values; it is pure and read-only. The adapter must receive an explicit tenant mapping, approved platform owner mapping, actor mapping, and approved project mapping where a legacy project is present. Legacy `open_id`, `user_id`, or owner identifiers are provenance, not platform principal IDs.
- The legacy SQLite `docs` table is a file-path/hash/classification/sync-metadata inventory, not the new platform's document or file-asset store. Its rows are migration candidates into the Knowledge domain only after source ownership, tenant/project ACL, content availability, classification, source hash, and document-version semantics are resolved. A row in `docs` does not prove that the referenced file exists, is authorized, or has been ingested/chunked/embedded.
- `docs` rows must not be inserted directly as `platform_file_assets`, `platform_documents`, chunks, or embeddings. A future Knowledge import must use the Knowledge ingestion contract and preserve the legacy row ID/path as provenance; missing content, ambiguous ownership, and missing ACL are quarantined rather than guessed. Filesystem paths are never exposed as platform download URLs.
- The new platform's canonical writes remain owned by `SessionAssetService` / tenant-bound PostgreSQL repositories for sessions, messages, and file assets, and by the Knowledge ingestion/repository boundary for documents. No legacy table is a fallback write store and no dual-write is permitted.
- Migration execution is a separate, explicitly approved operation: read-only inventory/export, versioned mapping file, deterministic/idempotent batch, row-level audit, diff/reconciliation, shadow-read validation, a single-writer cutover window, and a tested rollback plan. The adapter only maps records; it does not execute imports or choose cutover authority.
