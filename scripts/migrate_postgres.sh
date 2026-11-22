#!/usr/bin/env bash
set -euo pipefail

: "${PG_DSN:?PG_DSN must be set}"
MIGRATION_DIR="${MIGRATION_DIR:-agent_platform/storage/postgres}"
[[ -d "$MIGRATION_DIR" ]] || { echo "migration directory not found: $MIGRATION_DIR" >&2; exit 2; }
command -v psql >/dev/null
command -v sha256sum >/dev/null
command -v awk >/dev/null
[[ "$MIGRATION_DIR" != *"'"* ]] || { echo "migration path cannot contain a single quote" >&2; exit 2; }

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
RUNNER="$TEMP_DIR/runner.sql"
cat > "$RUNNER" <<'SQL'
\set ON_ERROR_STOP on
SELECT pg_advisory_lock(731942018);
CREATE TABLE IF NOT EXISTS platform_schema_migrations (
    migration_name text PRIMARY KEY,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

shopt -s nullglob
files=("$MIGRATION_DIR"/*.sql)
(("${#files[@]}" > 0)) || { echo "no SQL migrations found" >&2; exit 2; }

# Migration sequence numbers are immutable identities, not display labels.
declare -A seen_versions=()
last_version=0
known_names=()
# These duplicate numeric prefixes predate the immutable migration ledger.
# Keep their exact historical filenames/checksums; never permit new reuse.
legacy_duplicate_names=(
  "104_ai_value_management.sql"
  "104_knowledge_embedding_dimensions_1024.sql"
  "011_audit_append_only.sql"
  "011_registry_sessions_assets.sql"
  "021_alert_delivery_outbox.sql"
  "021_secret_versions.sql"
  "025_asset_connector_workers.sql"
  "025_workbench_task_execution.sql"
  "037_alert_delivery_outbox.sql"
  "037_queue_worker_concurrency_quotas.sql"
  "038_queue_worker_concurrency_quotas.sql"
  "038_queue_worker_quota_scopes.sql"
  "038_tool_effect_reconciliation.sql"
  "040_subject_deletion_ledger.sql"
  "040_tool_effect_reconciliation.sql"
  "041_queue_worker_concurrency_quotas.sql"
  "041_subject_deletion_ledger.sql"
  "041_tool_effect_reconciliation.sql"
  "042_quota_reservation_leases.sql"
  "042_root_run_quota_attribution.sql"
  "044_file_asset_versions.sql"
  "044_model_pricing_catalog.sql"
  "044_quota_context_window_budget.sql"
  "044_workbench_task_priority.sql"
  "045_file_processing_observability.sql"
  "045_workbench_task_priority.sql"
  "046_connector_sync_jobs.sql"
  "046_workbench_task_retry.sql"
  "048_resource_project_scope.sql"
  "048_run_context_snapshots.sql"
  "049_data_migration_staging.sql"
  "049_run_model_call_budget_ledger.sql"
  "050_quota_reservation_groups.sql"
  "050_run_budget_lineage.sql"
  "058_platform_credentials.sql"
  "058_run_context_snapshots.sql"
  "060_quota_reservation_leases.sql"
  "060_run_context_snapshots.sql"
  "071_data_lifecycle_audit.sql"
  "071_hierarchical_quota_policies.sql"
  "076_alert_operations_reliability.sql"
  "076_webhook_secret_revocations.sql"
  "077_outbox_delivery_dead_letter.sql"
  "077_public_api_idempotency.sql"
  "077_webhook_secret_revocation_leases.sql"
  "079_blackboard_event_delivery.sql"
  "079_knowledge_collections.sql"
  "079_prompt_audit_events.sql"
  "082_graph_storage.sql"
  "082_session_conversation_context.sql"
  "090_evaluation_schedule_leases.sql"
  "090_graph_storage.sql"
  "091_graph_extraction_candidates.sql"
  "091_graph_extraction_jobs.sql"
  "094_eval_variant_reports.sql"
  "094_eval_variant_runs.sql"
  "095_graph_candidate_file_source.sql"
  "095_graph_governance_persistence.sql"
)
for file in "${files[@]}"; do
  name="$(basename "$file")"
  [[ "$name" =~ ^([0-9]{3})_[A-Za-z0-9_]+\.sql$ ]] || {
    echo "invalid migration filename: $name" >&2
    exit 2
  }
  version="${BASH_REMATCH[1]}"
  if [[ -n "${seen_versions[$version]:-}" ]]; then
    previous="${seen_versions[$version]}"
    previous_is_legacy=false
    current_is_legacy=false
    for legacy_name in "${legacy_duplicate_names[@]}"; do
      [[ "$legacy_name" == "$previous" ]] && previous_is_legacy=true
      [[ "$legacy_name" == "$name" ]] && current_is_legacy=true
    done
    if [[ "$previous_is_legacy" != true || "$current_is_legacy" != true ]]; then
      echo "duplicate migration version $version: $name and $previous" >&2
      exit 2
    fi
  fi
  if ((10#$version <= last_version)); then
    echo "migration versions must be strictly increasing: $name" >&2
    exit 2
  fi
  seen_versions[$version]="$name"
  last_version=$((10#$version))
  known_names+=("$name")
done

# Never silently forget an applied migration after a rename or deletion.
known_sql=""
for name in "${known_names[@]}"; do
  [[ -z "$known_sql" ]] || known_sql+=","
  known_sql+="'$name'"
done
cat >> "$RUNNER" <<SQL
SELECT EXISTS(
  SELECT 1 FROM platform_schema_migrations
  WHERE migration_name NOT IN ($known_sql)
) AS removed_migration \\gset
\\if :removed_migration
  \\echo "An applied migration is missing or was renamed; restore its original filename"
  \\quit 4
\\endif
SQL

for file in "${files[@]}"; do
  name="$(basename "$file")"
  [[ "$name" =~ ^[0-9]{3}_[A-Za-z0-9_]+\.sql$ ]] || {
    echo "invalid migration filename: $name" >&2
    exit 2
  }
  checksum="$(sha256sum "$file" | awk '{print $1}')"
  prepared="$TEMP_DIR/$name"
  # Existing migrations carry their own outer BEGIN/COMMIT. Strip only those
  # boundary lines so the runner can commit the DDL and checksum ledger row
  # atomically in one transaction.
  awk '
    function update_dollar_state(line, rest, token) {
      rest = line
      while (match(rest, /\$[[:alnum:]_]*\$/)) {
        token = substr(rest, RSTART, RLENGTH)
        if (dollar_tag == "") dollar_tag = token
        else if (dollar_tag == token) dollar_tag = ""
        rest = substr(rest, RSTART + RLENGTH)
      }
    }
    {
      trimmed = $0
      sub(/^[[:space:]]+/, "", trimmed)
      sub(/[[:space:]]+$/, "", trimmed)
      keyword = toupper(trimmed)
      if (dollar_tag == "" && (keyword == "BEGIN;" || keyword == "COMMIT;")) {
        update_dollar_state($0)
        next
      }
      print
      update_dollar_state($0)
    }
  ' "$file" > "$prepared"
  cat >> "$RUNNER" <<SQL
SELECT EXISTS(
  SELECT 1 FROM platform_schema_migrations WHERE migration_name='$name'
) AS migration_exists \gset
\if :migration_exists
  SELECT checksum='$checksum' AS checksum_matches
    FROM platform_schema_migrations WHERE migration_name='$name' \gset
  \if :checksum_matches
    \echo "Already applied: $name"
  \else
    \echo "Checksum changed for applied migration: $name"
    \quit 3
  \endif
\else
  \echo "Applying: $name"
  BEGIN;
  \i '$prepared'
  INSERT INTO platform_schema_migrations(migration_name,checksum)
    VALUES ('$name','$checksum');
  COMMIT;
\endif
SQL
done
cat >> "$RUNNER" <<'SQL'
SELECT pg_advisory_unlock(731942018);
SQL

psql "$PG_DSN" -X -v ON_ERROR_STOP=1 -f "$RUNNER"
