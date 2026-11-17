BEGIN;

CREATE TABLE IF NOT EXISTS platform_tenant_locale_preferences (
    tenant_id text PRIMARY KEY REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    language text NOT NULL CHECK (language IN ('zh-CN', 'en-US', 'ja-JP', 'de-DE', 'fr-FR')),
    timezone_name text NOT NULL,
    region char(2) NOT NULL CHECK (region ~ '^[A-Z]{2}$'),
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
