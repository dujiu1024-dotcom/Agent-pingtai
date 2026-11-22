-- Add provenance-backed historical workload volume to human baselines.
ALTER TABLE ai_value_baselines
    ADD COLUMN IF NOT EXISTS historical_business_volume BIGINT,
    ADD COLUMN IF NOT EXISTS volume_period_start DATE,
    ADD COLUMN IF NOT EXISTS volume_period_end DATE,
    ADD COLUMN IF NOT EXISTS volume_source TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_ai_value_baselines_volume_provenance'
    ) THEN
        ALTER TABLE ai_value_baselines
            ADD CONSTRAINT ck_ai_value_baselines_volume_provenance
            CHECK (
                (historical_business_volume IS NULL
                 AND volume_period_start IS NULL
                 AND volume_period_end IS NULL
                 AND volume_source IS NULL)
                OR
                (historical_business_volume IS NOT NULL
                 AND historical_business_volume >= 0
                 AND volume_period_start IS NOT NULL
                 AND volume_period_end IS NOT NULL
                 AND volume_period_end > volume_period_start
                 AND volume_source IS NOT NULL
                 AND length(btrim(volume_source)) > 0)
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_ai_value_baselines_volume_period
    ON ai_value_baselines (tenant_id, process_id, role_id, volume_period_start, volume_period_end);
