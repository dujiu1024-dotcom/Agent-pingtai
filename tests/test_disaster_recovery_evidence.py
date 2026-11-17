from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "disaster_recovery.py"
POLICY = ROOT / "deploy" / "disaster-recovery-policy.json"


def make_record() -> dict:
    incident = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    return {
        "exercise_id": "DR-2026-001",
        "scope": "full_platform",
        "incident_declared_at": incident.isoformat(),
        "last_known_good_write_at": (incident - timedelta(minutes=30)).isoformat(),
        "traffic_resumed_at": (incident + timedelta(hours=2)).isoformat(),
        "component_checks": {name: True for name in ("config", "postgres", "redis", "objects")},
        "consistency_checks": {
            "database_object_references_reconciled": True,
            "outbox_and_event_state_reconciled": True,
            "tenant_authorization_smoke_tests_passed": True,
        },
        "failover_checks": {
            "former_primary_fenced": True,
            "replication_or_backup_recovery_point_recorded": True,
            "routing_and_health_checks_validated": True,
        },
        "reviewer_signoff": True,
        "evidence_references": ["incident://DR-2026-001/timeline", "artifact://manifest-set-1"],
    }


def run_record(tmp_path: Path, record: dict) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "record.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--policy", str(POLICY), "--validate-record", str(path)],
        capture_output=True, text=True, check=False,
    )


def test_recovery_plan_exposes_order_and_objectives() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--plan"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["recovery_order"].index("restore_postgres") < plan["recovery_order"].index("restore_redis")
    assert plan["recovery_order"].index("restore_redis") < plan["recovery_order"].index("restore_objects")
    assert plan["objectives"]["full_platform"]["rto_seconds"] == 28800


def test_valid_drill_record_computes_rpo_rto_and_passes(tmp_path: Path) -> None:
    result = run_record(tmp_path, make_record())
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["measured_rpo_seconds"] == 1800
    assert report["measured_rto_seconds"] == 7200


def test_record_fails_when_rpo_exceeds_target(tmp_path: Path) -> None:
    record = make_record()
    incident = datetime.fromisoformat(record["incident_declared_at"])
    record["last_known_good_write_at"] = (incident - timedelta(hours=25)).isoformat()
    result = run_record(tmp_path, record)
    assert result.returncode == 1
    assert "measured RPO exceeds approved target" in json.loads(result.stdout)["failures"]


def test_full_platform_record_requires_all_component_and_consistency_gates(tmp_path: Path) -> None:
    record = make_record()
    record["component_checks"]["objects"] = False
    record["consistency_checks"]["outbox_and_event_state_reconciled"] = False
    result = run_record(tmp_path, record)
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert any("objects" in item for item in report["failures"])
    assert any("outbox_and_event_state" in item for item in report["failures"])


def test_full_platform_record_requires_fencing_reviewer_and_evidence(tmp_path: Path) -> None:
    record = make_record()
    record["failover_checks"]["former_primary_fenced"] = False
    record["reviewer_signoff"] = False
    record["evidence_references"] = []
    result = run_record(tmp_path, record)
    assert result.returncode == 1
    failures = json.loads(result.stdout)["failures"]
    assert any("former_primary_fenced" in item for item in failures)
    assert "independent reviewer sign-off missing" in failures
    assert "evidence_references must contain at least one non-empty reference" in failures


def test_timestamp_without_timezone_is_rejected(tmp_path: Path) -> None:
    record = make_record()
    record["incident_declared_at"] = "2026-01-01T12:00:00"
    result = run_record(tmp_path, record)
    assert result.returncode == 2
    assert "must include a timezone" in result.stderr
