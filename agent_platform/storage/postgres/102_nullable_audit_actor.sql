-- Instance/no-auth HTTP requests have no user identity. Preserve the
-- append-only audit ledger while representing that fact as SQL NULL, never a
-- synthetic or default user ID.
BEGIN;
ALTER TABLE platform_identity_audit
    ALTER COLUMN actor_id DROP NOT NULL;
COMMIT;
