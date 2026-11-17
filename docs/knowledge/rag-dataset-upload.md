# RAG dataset upload and indexing (C-24-01)

The authenticated Knowledge upload endpoint is the ingestion boundary reused by the RAG playground:

- GET /api/platform/v1/knowledge/upload-options reports parser media types, upload limit, chunk defaults, and the configured embedding model.
- POST /api/platform/v1/knowledge/upload?title=...&max_chars=1200&overlap=160 accepts the raw file body; set the request Content-Type to the file media type. Optional query parameters scope, project_id, collection_id, and source_uri preserve Knowledge visibility and collection boundaries.

Built-in parsers cover plain text, Markdown, HTML/XHTML, PDF, DOCX, XLSX and PPTX. Additional OCR support remains an explicit host-injected parser; no OCR service is silently called. The upload path reuses KnowledgeService.ingest, which cleans and hashes extracted text, chunks with source/page offsets, persists the immutable document version, and writes embeddings through the configured embedder/repository.

The response includes the applied chunking parameters and index_status:
- indexed means the configured embedder completed and the repository accepted all chunk vectors.
- pending_embedding means the host has not configured an embedder; upload persistence succeeded, but the dataset is not yet query-ready.

Invalid chunk sizes/overlap and unsupported media types fail before document persistence. Tenant identity and ownership are derived from the authenticated KnowledgePrincipal, never from upload fields.
