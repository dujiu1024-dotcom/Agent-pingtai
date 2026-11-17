-- Reserved migration slot retained for deployed ordering compatibility.
-- The canonical platform_api_idempotency schema is created by
-- 078_api_idempotency.sql; keeping this migration side-effect free avoids
-- creating an incompatible table before the canonical migration runs.
BEGIN;
COMMIT;
