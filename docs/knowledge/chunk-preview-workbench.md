# C-24-02 Chunk preview workbench

The workbench exposes a Chunk Preview view over the existing authenticated
Knowledge APIs:

- `GET /api/platform/v1/knowledge/documents` lists documents visible to the
  authenticated KnowledgePrincipal.
- `GET /api/platform/v1/knowledge/documents/{document_id}/chunks` returns
  chunk text, token/character counts, page and offset provenance, content hash,
  and embedding status after the service applies document and chunk ACL checks.
- `PATCH /api/platform/v1/knowledge/documents/{document_id}/chunks/{chunk_id}`
  saves an edited chunk through the existing document-management authorization
  path. The API marks the embedding stale; this UI does not claim the vector
  has been regenerated.

The UI does not accept tenant/user identity from the browser. All data access
uses the same-origin authenticated platform API and its CSRF-aware transport.

## Remaining acceptance boundary

This increment provides preview and manual editing. The current repository
contract does not expose a safe chunk-delete or document-rechunk API, and the
UI deliberately does not simulate either action or delete a whole document as
a substitute. Those operations require an explicit persistence/embedding
lifecycle contract and remain open under C-24-02. Browser/device acceptance
and a configured live Knowledge provider remain deployment checks.
