-- 放宽死信表约束。
-- 旧约束要求 attempt_count >= max_attempts，只允许“重试耗尽”入队；
-- 执行器直接判定失败的运行同样需要可人工重放，因此只保留计数有效性校验。
BEGIN;

ALTER TABLE platform_run_dead_letters
    DROP CONSTRAINT IF EXISTS platform_run_dead_letters_check;

ALTER TABLE platform_run_dead_letters
    ADD CONSTRAINT platform_run_dead_letters_check
    CHECK (attempt_count >= 1 AND max_attempts >= 1);

COMMIT;
