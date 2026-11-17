BEGIN;

-- Approval records are bound to the trusted policy revision under which they
-- were requested. A policy revision change makes existing approvals unusable.
ALTER TABLE platform_approvals
    ADD COLUMN IF NOT EXISTS policy_version text NOT NULL DEFAULT '1';

ALTER TABLE platform_approvals
    DROP CONSTRAINT IF EXISTS platform_approvals_policy_version_nonempty;

ALTER TABLE platform_approvals
    ADD CONSTRAINT platform_approvals_policy_version_nonempty
    CHECK (length(btrim(policy_version)) > 0);

COMMIT;
