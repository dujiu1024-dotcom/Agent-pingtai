-- Add concurrency-only scopes for shared queues and worker pools.
-- Keep the Run scope introduced by the root-run attribution migration. This
-- migration is also replayed by integration fixtures after later migrations.
BEGIN;

ALTER TABLE platform_quota_budgets
    DROP CONSTRAINT IF EXISTS platform_quota_budgets_scope_type_check;
ALTER TABLE platform_quota_budgets
    ADD CONSTRAINT platform_quota_budgets_scope_type_check
    CHECK (scope_type IN ('tenant','project','agent','model','run','queue','worker_pool')) NOT VALID;

COMMIT;
