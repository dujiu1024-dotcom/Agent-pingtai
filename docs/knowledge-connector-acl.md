# Knowledge connector ACL contract

Google Drive and SharePoint document factories must emit documents with:

- `scope=KnowledgeScope.PRIVATE`
- `owner_user_id` set to a platform user ID
- `metadata["_knowledge_acl"]` set to a list of platform ACL grants

Each grant has `principal_type` (`user`, `role`, or `project`), `principal_id` (a platform identity ID), and `permission` (`read` or `manage`). External directory identifiers must not be copied directly into platform ACLs. Resolve them through the host identity directory with `map_google_drive_permissions` or `map_sharepoint_permissions` from `agent_platform.knowledge.acl_mapping`.

The mapping helpers reject public, domain-wide, anonymous-link, unknown-role, and unresolved identity grants. A host may deliberately support those cases only by implementing an explicit, auditable mapping into platform identities; it must not silently broaden access.

During connector sync, the KnowledgeService validates the ACL before ingesting content, atomically replaces the document's ACL grants, and advances the connector cursor only after content and ACL work succeeds. An empty ACL is enforced as owner-only access. If a previously synchronized source document can no longer be mapped safely, its indexed copy is deleted rather than retaining stale permissions.

The repository adapter must implement `replace_document_access(tenant_id, document_id, grants, granted_by)`. The PostgreSQL implementation enables ACL enforcement and replaces grants in one transaction; the in-memory implementation mirrors the same behavior for tests.

This contract does not complete host OAuth consent/callback configuration, tenant-specific provider construction, or live external-directory identity lookup. Those remain deployment composition responsibilities and require real-tenant verification.
