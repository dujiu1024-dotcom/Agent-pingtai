-- Persist temporal applicability bounds for current and immutable memory versions.
BEGIN;

ALTER TABLE platform_memories
    ADD COLUMN IF NOT EXISTS applies_from timestamptz,
    ADD COLUMN IF NOT EXISTS applies_until timestamptz;

ALTER TABLE platform_memories
    DROP CONSTRAINT IF EXISTS platform_memories_applicability_bounds_check;
ALTER TABLE platform_memories
    ADD CONSTRAINT platform_memories_applicability_bounds_check
    CHECK (applies_from IS NULL OR applies_until IS NULL OR applies_from < applies_until);

ALTER TABLE platform_memory_versions
    ADD COLUMN IF NOT EXISTS applies_from timestamptz,
    ADD COLUMN IF NOT EXISTS applies_until timestamptz;

ALTER TABLE platform_memory_versions
    DROP CONSTRAINT IF EXISTS platform_memory_versions_applicability_bounds_check;
ALTER TABLE platform_memory_versions
    ADD CONSTRAINT platform_memory_versions_applicability_bounds_check
    CHECK (applies_from IS NULL OR applies_until IS NULL OR applies_from < applies_until);

COMMIT;
