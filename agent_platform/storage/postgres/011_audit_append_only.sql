-- Audit records are append-only for application/runtime roles.
-- Apply with the platform migration runner. The runtime DB role must not own
-- these tables or have DDL/TRIGGER privileges; migration ownership stays separate.
BEGIN;

CREATE OR REPLACE FUNCTION platform_reject_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'platform audit records are append-only'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_platform_tool_audit_append_only ON platform_tool_audit;
CREATE TRIGGER trg_platform_tool_audit_append_only
    BEFORE UPDATE OR DELETE ON platform_tool_audit
    FOR EACH ROW EXECUTE FUNCTION platform_reject_audit_mutation();

DROP TRIGGER IF EXISTS trg_platform_identity_audit_append_only ON platform_identity_audit;
CREATE TRIGGER trg_platform_identity_audit_append_only
    BEFORE UPDATE OR DELETE ON platform_identity_audit
    FOR EACH ROW EXECUTE FUNCTION platform_reject_audit_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON platform_tool_audit FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE ON platform_identity_audit FROM PUBLIC;

COMMIT;
