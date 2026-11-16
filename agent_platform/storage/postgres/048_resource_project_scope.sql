-- C-8-05: project scope for connectors; explicit legacy/null-scope boundary.
-- Existing session and file rows already carry project_id. Null remains tenant scope;
-- this migration never guesses or backfills a project from legacy conversation data.
BEGIN;

ALTER TABLE platform_data_connections
    ADD COLUMN IF NOT EXISTS project_id text;

CREATE INDEX IF NOT EXISTS idx_data_connections_project
    ON platform_data_connections (tenant_id, project_id, status);

CREATE INDEX IF NOT EXISTS idx_platform_sessions_project
    ON platform_conversation_sessions (tenant_id, project_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_platform_assets_project
    ON platform_file_assets (tenant_id, project_id, created_at DESC);

COMMENT ON COLUMN platform_data_connections.project_id IS
    'NULL means tenant-scoped connection; non-NULL is restricted to that project. Legacy rows remain NULL until explicitly mapped by an authorized migration.';

COMMENT ON COLUMN platform_conversation_sessions.project_id IS
    'NULL means tenant-scoped session; project membership is never inferred from legacy conversation tables.';

COMMENT ON COLUMN platform_file_assets.project_id IS
    'NULL means tenant-scoped asset; project membership is inherited from a linked session or explicitly assigned.';

COMMIT;
