from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "continuous_runtime_drill.py"
POLICY = ROOT / "deploy" / "disaster-recovery-policy.json"
COMPONENTS = ("config", "postgres", "redis", "objects")
CHECKS = (
    "durable_task_checkpoint_restored",
    "worker_lease_takeover_verified",
    "outbox_replay_event_ids_preserved",
    "consumer_receipts_prevented_duplicate_effects",
    "run_context_and_trace_restored",
    "unresolved_runs_handed_off",
    "postgres_redis_objects_restore_verified",
    "vector_index_rebuilt_or_reconciled",
    "audit_chain_continuity_verified",
    "tenant_isolation_verified",
    "former_primary_fenced",
    "controlled_failback_verified",
)


def make_record() -> dict:
    incident = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    snapshot = (incident - timedelta(minutes=10)).isoformat()
    backups = {
        component: {
            "artifact": f"{component}.backup.age",
            "manifest_ref": f"evidence://{component}.manifest.json",
            "sha256": "a" * 64,
            "created_at_utc": snapshot,
            "checkpoint_id": "checkpoint-001",
            "restored": True,
        }
        for component in COMPONENTS
    }
    return {
        "exercise_id": "DR-CONTINUITY-001",
        "incident_declared_at": incident.isoformat(),
        "last_known_good_write_at": (incident - timedelta(minutes=20)).isoformat(),
        "traffic_resumed_at": (incident + timedelta(hours=2)).isoformat(),
        "component_backups": backups,
        "continuity_checks": {name: True for name in CHECKS},
        "pending_runs": [
            {"run_id": "run-1", "disposition": "resumed", "idempotency_key_preserved": True},
            {"run_id": "run-2", "disposition": "human_handoff", "idempotency_key_preserved": True},
        ],
        "duplicate_external_effects": 0,
        "reviewer_signoff": True,
        "evidence_references": ["evidence://timeline", "evidence://replay-receipts"],
    }


def run_record(tmp_path: Path, record: dict) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "continuity-record.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--policy", str(POLICY), "--validate-record", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_plan_lists_backup_replay_takeover_and_recovery_order() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--plan"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert "worker_lease_takeover_verified" in plan["continuity_checks"]
    assert "outbox_replay_event_ids_preserved" in plan["continuity_checks"]
    assert plan["backup_components"] == list(COMPONENTS)
    assert plan["safety_boundary"].startswith("Evidence planner/validator only")


def test_valid_continuity_record_measures_rpo_rto_and_passes(tmp_path: Path) -> None:
    result = run_record(tmp_path, make_record())
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["measured_rpo_seconds"] == 1200
    assert report["measured_rto_seconds"] == 7200
    assert report["pending_run_count"] == 2
    assert report["duplicate_external_effects"] == 0


def test_missing_backup_or_continuity_gate_fails(tmp_path: Path) -> None:
    record = make_record()
    del record["component_backups"]["redis"]
    record["continuity_checks"]["worker_lease_takeover_verified"] = False
    result = run_record(tmp_path, record)
    assert result.returncode == 1
    failures = json.loads(result.stdout)["failures"]
    assert "backup evidence missing: redis" in failures
    assert "continuity check missing or failed: worker_lease_takeover_verified" in failures


def test_uncoordinated_backup_checkpoints_fail(tmp_path: Path) -> None:
    record = make_record()
    record["component_backups"]["objects"]["checkpoint_id"] = "different-checkpoint"
    result = run_record(tmp_path, record)
    assert result.returncode == 1
    assert "component backups do not share one coordinated checkpoint_id" in json.loads(result.stdout)["failures"]


def test_snapshot_skew_and_invalid_checksum_fail(tmp_path: Path) -> None:
    record = make_record()
    record["component_backups"]["objects"]["created_at_utc"] = (
        datetime(2026, 1, 2, 12, tzinfo=timezone.utc).isoformat()
    )
    record["component_backups"]["redis"]["sha256"] = "not-a-checksum"
    result = run_record(tmp_path, record)
    failures = json.loads(result.stdout)["failures"]
    assert result.returncode == 1
    assert "component backup snapshot skew exceeds policy" in failures
    assert "redis backup sha256 invalid" in failures


def test_pending_run_must_be_resumed_or_handed_off_idempotently(tmp_path: Path) -> None:
    record = make_record()
    record["pending_runs"][0]["disposition"] = "dropped"
    record["pending_runs"][0]["idempotency_key_preserved"] = False
    record["duplicate_external_effects"] = 1
    result = run_record(tmp_path, record)
    failures = json.loads(result.stdout)["failures"]
    assert result.returncode == 1
    assert any("must be resumed or handed off" in item for item in failures)
    assert any("idempotency key not preserved" in item for item in failures)
    assert "duplicate_external_effects must be integer zero" in failures


def test_rpo_rto_and_reviewer_are_mandatory(tmp_path: Path) -> None:
    record = make_record()
    incident = datetime.fromisoformat(record["incident_declared_at"])
    record["last_known_good_write_at"] = (incident - timedelta(days=2)).isoformat()
    record["traffic_resumed_at"] = (incident + timedelta(days=2)).isoformat()
    record["reviewer_signoff"] = False
    result = run_record(tmp_path, record)
    failures = json.loads(result.stdout)["failures"]
    assert result.returncode == 1
    assert "measured RPO exceeds approved full-platform target" in failures
    assert "measured RTO exceeds approved full-platform target" in failures
    assert "independent reviewer sign-off missing" in failures


def test_naive_timestamps_are_rejected(tmp_path: Path) -> None:
    record = make_record()
    record["incident_declared_at"] = "2026-01-01T12:00:00"
    result = run_record(tmp_path, record)
    assert result.returncode == 2
    assert "must include a timezone" in result.stderr
