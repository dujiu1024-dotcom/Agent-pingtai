-- Once a document opts into resource ACLs, removing its last grant must not
-- silently restore tenant-wide visibility. Existing documents retain legacy
-- visibility until an ACL grant is explicitly created.
BEGIN;
ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS acl_enforced boolean NOT NULL DEFAULT false;

UPDATE knowledge_documents d
SET acl_enforced = true
WHERE EXISTS (
    SELECT 1 FROM knowledge_document_acl a
    WHERE a.tenant_id = d.tenant_id AND a.document_id = d.document_id
);
COMMIT;
