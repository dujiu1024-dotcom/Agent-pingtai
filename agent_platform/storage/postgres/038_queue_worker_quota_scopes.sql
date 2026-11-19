BEGIN;

ALTER TABLE platform_quota_budgets
  DROP CONSTRAINT IF EXISTS platform_quota_budgets_scope_type_check,
  DROP CONSTRAINT IF EXISTS platform_quota_queue_worker_concurrency_only;

ALTER TABLE platform_quota_budgets
  ADD CONSTRAINT platform_quota_budgets_scope_type_check
    CHECK (scope_type IN ('tenant','project','agent','model','run','queue','worker_pool')),
  ADD CONSTRAINT platform_quota_queue_worker_concurrency_only
    CHECK (
      scope_type NOT IN ('queue','worker_pool')
      OR (
        hard_concurrency IS NOT NULL
        AND hard_tokens IS NULL
        AND hard_cost_usd IS NULL
        AND soft_tokens IS NULL
        AND soft_cost_usd IS NULL
      )
    );

COMMIT;
