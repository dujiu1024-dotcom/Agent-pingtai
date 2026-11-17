-- Immutable file object history with a tenant-scoped current-version pointer.
BEGIN;

ALTER TABLE platform_file_assets
    ADD COLUMN IF NOT EXISTS current_version integer NOT NULL DEFAULT 1
    CHECK (current_version > 0);

CREATE TABLE IF NOT EXISTS platform_file_asset_versions (
    tenant_id       text NOT NULL,
    asset_id        uuid NOT NULL,
    version         integer NOT NULL CHECK (version > 0),
    object_key      text NOT NULL,
    filename        text NOT NULL,
    media_type      text NOT NULL,
    size_bytes      bigint NOT NULL CHECK (size_bytes > 0),
    sha256          char(64) NOT NULL,
    status          text NOT NULL
                    CHECK (status IN ('pending','scanning','available','rejected','deleted')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, asset_id, version),
    UNIQUE (tenant_id, object_key),
    FOREIGN KEY (tenant_id, asset_id)
        REFERENCES platform_file_assets(tenant_id, asset_id)
        ON DELETE CASCADE
);

INSERT INTO platform_file_asset_versions
    (tenant_id,asset_id,version,object_key,filename,media_type,size_bytes,sha256,status,created_at)
SELECT tenant_id,asset_id,1,object_key,filename,media_type,size_bytes,sha256,status,created_at
FROM platform_file_assets
ON CONFLICT (tenant_id,asset_id,version) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_platform_file_asset_versions_history
    ON platform_file_asset_versions (tenant_id, asset_id, version DESC);

COMMIT;
