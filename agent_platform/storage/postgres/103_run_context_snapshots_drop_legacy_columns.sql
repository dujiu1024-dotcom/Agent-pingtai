-- 060 已把 Run 上下文快照切换到 context_payload/references_payload，但旧迁移
-- （048/058）创建的 context/reference_window 列仍保留 NOT NULL 约束，导致新
-- 写入路径无法插入。旧列已无代码引用，这里直接移除。
BEGIN;

ALTER TABLE platform_run_context_snapshots
    DROP COLUMN IF EXISTS context;
ALTER TABLE platform_run_context_snapshots
    DROP COLUMN IF EXISTS reference_window;

COMMIT;
