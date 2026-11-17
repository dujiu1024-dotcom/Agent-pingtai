-- Canonical budget lineage for root, retried and delegated Runs.
BEGIN;
ALTER TABLE platform_runs
    ADD COLUMN IF NOT EXISTS retry_of_run_id uuid,
    ADD COLUMN IF NOT EXISTS parent_run_id uuid,
    ADD COLUMN IF NOT EXISTS budget_root_run_id uuid,
    ADD COLUMN IF NOT EXISTS budget_lineage_kind text NOT NULL DEFAULT 'root',
    ADD COLUMN IF NOT EXISTS budget_lineage_key text;
WITH RECURSIVE run_roots(tenant_id, run_id, root_run_id) AS (
    SELECT tenant_id, run_id, run_id FROM platform_runs WHERE retry_of_run_id IS NULL
    UNION ALL
    SELECT child.tenant_id, child.run_id, parent.root_run_id
      FROM platform_runs child JOIN run_roots parent
        ON parent.tenant_id=child.tenant_id AND parent.run_id=child.retry_of_run_id
)
UPDATE platform_runs r SET budget_root_run_id=roots.root_run_id,
    parent_run_id=CASE WHEN r.retry_of_run_id IS NOT NULL THEN r.retry_of_run_id ELSE r.parent_run_id END,
    budget_lineage_kind=CASE WHEN r.retry_of_run_id IS NOT NULL THEN 'retry' ELSE 'root' END,
    budget_lineage_key=CASE WHEN r.retry_of_run_id IS NOT NULL THEN 'retry:'||r.run_id::text ELSE r.budget_lineage_key END
  FROM run_roots roots WHERE roots.tenant_id=r.tenant_id AND roots.run_id=r.run_id AND r.budget_root_run_id IS NULL;
UPDATE platform_runs SET budget_root_run_id=run_id WHERE budget_root_run_id IS NULL;
ALTER TABLE platform_runs ALTER COLUMN budget_root_run_id SET NOT NULL;
ALTER TABLE platform_runs ADD CONSTRAINT fk_platform_run_retry_source FOREIGN KEY (tenant_id,retry_of_run_id) REFERENCES platform_runs(tenant_id,run_id);
ALTER TABLE platform_runs ADD CONSTRAINT fk_platform_run_budget_parent FOREIGN KEY (tenant_id,parent_run_id) REFERENCES platform_runs(tenant_id,run_id);
ALTER TABLE platform_runs ADD CONSTRAINT fk_platform_run_budget_root FOREIGN KEY (tenant_id,budget_root_run_id) REFERENCES platform_runs(tenant_id,run_id);
ALTER TABLE platform_runs ADD CONSTRAINT ck_platform_run_budget_lineage_kind CHECK (budget_lineage_kind IN ('root','retry','delegation'));
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_run_budget_child_key ON platform_runs(tenant_id,parent_run_id,budget_lineage_kind,budget_lineage_key) WHERE parent_run_id IS NOT NULL AND budget_lineage_key IS NOT NULL;
CREATE OR REPLACE FUNCTION platform_enforce_run_budget_lineage() RETURNS trigger LANGUAGE plpgsql AS $lineage$
DECLARE inherited_root uuid;
BEGIN
    IF NEW.retry_of_run_id IS NOT NULL THEN
        SELECT budget_root_run_id INTO inherited_root FROM platform_runs WHERE tenant_id=NEW.tenant_id AND run_id=NEW.retry_of_run_id FOR UPDATE;
        IF inherited_root IS NULL THEN RAISE EXCEPTION 'retry source Run has no budget lineage'; END IF;
        IF NEW.parent_run_id IS NOT NULL AND NEW.parent_run_id<>NEW.retry_of_run_id THEN RAISE EXCEPTION 'retry parent must be the source Run'; END IF;
        NEW.parent_run_id:=NEW.retry_of_run_id; NEW.budget_lineage_kind:='retry';
        IF NEW.budget_lineage_key IS NULL THEN NEW.budget_lineage_key:='retry:'||NEW.run_id::text; END IF;
    ELSIF NEW.parent_run_id IS NOT NULL THEN
        SELECT budget_root_run_id INTO inherited_root FROM platform_runs WHERE tenant_id=NEW.tenant_id AND run_id=NEW.parent_run_id FOR UPDATE;
        IF inherited_root IS NULL THEN RAISE EXCEPTION 'parent Run not found in tenant or has no budget lineage'; END IF;
        IF NEW.budget_lineage_kind='root' THEN NEW.budget_lineage_kind:='delegation'; END IF;
    ELSE inherited_root:=NEW.run_id; NEW.budget_lineage_kind:='root';
    END IF;
    IF TG_OP='UPDATE' AND OLD.parent_run_id IS NULL AND OLD.retry_of_run_id IS NULL
       AND OLD.budget_root_run_id=OLD.run_id AND NEW.parent_run_id IS NOT NULL THEN
        NEW.budget_root_run_id:=NULL;
    END IF;
    IF NEW.budget_root_run_id IS NOT NULL AND NEW.budget_root_run_id<>inherited_root THEN RAISE EXCEPTION 'Run cannot reset or change its inherited budget root'; END IF;
    NEW.budget_root_run_id:=inherited_root; RETURN NEW;
END;
$lineage$;
DROP TRIGGER IF EXISTS trg_platform_run_budget_lineage ON platform_runs;
CREATE TRIGGER trg_platform_run_budget_lineage BEFORE INSERT OR UPDATE OF parent_run_id,retry_of_run_id,budget_root_run_id ON platform_runs FOR EACH ROW EXECUTE FUNCTION platform_enforce_run_budget_lineage();
COMMIT;
