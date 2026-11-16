-- Connector cursor concurrency and operational diagnostics.
BEGIN;
ALTER TABLE platform_data_connections
    ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error text;
COMMIT;
