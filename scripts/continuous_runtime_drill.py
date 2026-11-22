#!/usr/bin/env python3
"""Plan and validate continuous-runtime HA/DR drill evidence; never touches infrastructure."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "deploy" / "disaster-recovery-policy.json"
REQUIRED_BACKUPS = ("config", "postgres", "redis", "objects")
REQUIRED_CONTINUITY_CHECKS = (
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


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def drill_plan(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": "continuous_runtime_full_platform",
        "objectives": policy["objectives"]["full_platform"],
        "backup_components": list(REQUIRED_BACKUPS),
        "continuity_checks": list(REQUIRED_CONTINUITY_CHECKS),
        "sequence": [
            "freeze_writers_and_consumers; record checkpoint_id and last_good_write",
            "backup PostgreSQL, Redis, object storage and deployment config",
            "verify artifact checksums, manifests, encryption and snapshot timestamps",
            "restore PostgreSQL, then Redis, then object storage and configuration",
            "reconcile database-object references, outbox, vector index and audit chain",
            "recover durable task checkpoints and acquire worker leases on recovery node",
            "replay bounded outbox events with stable event IDs and durable consumer receipts",
            "restore Run context/trace; resume idempotent work or hand off to a human",
            "fence former primary; validate canary; perform controlled failback",
            "record timestamps, measured RPO/RTO, evidence references and reviewer sign-off",
        ],
        "safety_boundary": (
            "Evidence planner/validator only. It does not stop services, run backups, "
            "restore data, promote nodes, replay messages or change traffic."
        ),
    }


def validate_record(record: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    incident = parse_time(record.get("incident_declared_at"), "incident_declared_at")
    last_good = parse_time(record.get("last_known_good_write_at"), "last_known_good_write_at")
    resumed = parse_time(record.get("traffic_resumed_at"), "traffic_resumed_at")
    if last_good > incident:
        raise ValueError("last_known_good_write_at cannot be after incident_declared_at")
    if resumed < incident:
        raise ValueError("traffic_resumed_at cannot be before incident_declared_at")

    measured_rpo = round((incident - last_good).total_seconds(), 3)
    measured_rto = round((resumed - incident).total_seconds(), 3)
    objective = policy["objectives"]["full_platform"]
    failures: list[str] = []
    for field in ("environment", "topology", "platform_version", "fault_injection_scenario"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            failures.append(f"{field} must describe the exercised environment")

    backups = record.get("component_backups")
    if not isinstance(backups, dict):
        backups = {}
        failures.append("component_backups must be an object")
    checkpoint_ids: set[str] = set()
    snapshot_times: list[datetime] = []
    for component in REQUIRED_BACKUPS:
        item = backups.get(component)
        if not isinstance(item, dict):
            failures.append(f"backup evidence missing: {component}")
            continue
        for field in ("artifact", "manifest_ref", "checkpoint_id"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                failures.append(f"{component} backup {field} missing")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            failures.append(f"{component} backup sha256 invalid")
        if item.get("restored") is not True:
            failures.append(f"{component} restore not verified")
        checkpoint = item.get("checkpoint_id")
        if isinstance(checkpoint, str) and checkpoint.strip():
            checkpoint_ids.add(checkpoint)
        try:
            snapshot_times.append(parse_time(item.get("created_at_utc"), f"{component}.created_at_utc"))
        except ValueError as exc:
            failures.append(str(exc))

    if len(checkpoint_ids) != 1:
        failures.append("component backups do not share one coordinated checkpoint_id")
    max_skew = int(policy.get("max_component_snapshot_skew_seconds", 86400))
    if len(snapshot_times) == len(REQUIRED_BACKUPS):
        skew = (max(snapshot_times) - min(snapshot_times)).total_seconds()
        if skew > max_skew:
            failures.append("component backup snapshot skew exceeds policy")

    checks = record.get("continuity_checks")
    if not isinstance(checks, dict):
        checks = {}
        failures.append("continuity_checks must be an object")
    for gate in REQUIRED_CONTINUITY_CHECKS:
        if checks.get(gate) is not True:
            failures.append(f"continuity check missing or failed: {gate}")

    pending_runs = record.get("pending_runs")
    if not isinstance(pending_runs, list):
        failures.append("pending_runs must be a list (empty when no pending Runs)")
        pending_runs = []
    for index, run in enumerate(pending_runs):
        if not isinstance(run, dict) or not isinstance(run.get("run_id"), str) or not run["run_id"].strip():
            failures.append(f"pending_runs[{index}] has no run_id")
            continue
        if run.get("disposition") not in ("resumed", "human_handoff"):
            failures.append(f"pending_runs[{index}] must be resumed or handed off")
        if run.get("idempotency_key_preserved") is not True:
            failures.append(f"pending_runs[{index}] idempotency key not preserved")

    duplicate_effects = record.get("duplicate_external_effects")
    if isinstance(duplicate_effects, bool) or not isinstance(duplicate_effects, int) or duplicate_effects != 0:
        failures.append("duplicate_external_effects must be integer zero")

    evidence = record.get("evidence_references")
    if not isinstance(evidence, list) or not evidence or any(
        not isinstance(item, str) or not item.strip() for item in evidence
    ):
        failures.append("evidence_references must contain non-empty references")
    if record.get("reviewer_signoff") is not True:
        failures.append("independent reviewer sign-off missing")

    rpo_target = int(objective["rpo_seconds"])
    rto_target = int(objective["rto_seconds"])
    if measured_rpo > rpo_target:
        failures.append("measured RPO exceeds approved full-platform target")
    if measured_rto > rto_target:
        failures.append("measured RTO exceeds approved full-platform target")

    return {
        "exercise_id": record.get("exercise_id"),
        "scope": "continuous_runtime_full_platform",
        "measured_rpo_seconds": measured_rpo,
        "measured_rto_seconds": measured_rto,
        "target_rpo_seconds": rpo_target,
        "target_rto_seconds": rto_target,
        "backup_snapshot_skew_seconds": (
            round((max(snapshot_times) - min(snapshot_times)).total_seconds(), 3)
            if len(snapshot_times) == len(REQUIRED_BACKUPS) else None
        ),
        "component_checkpoint_ids": sorted(checkpoint_ids),
        "pending_run_count": len(pending_runs),
        "duplicate_external_effects": duplicate_effects,
        "passed": not failures,
        "failures": failures,
        "note": (
            "A passing report validates supplied drill evidence only. It is not a "
            "production HA/DR guarantee or proof that infrastructure actions occurred."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="print the controlled drill plan")
    mode.add_argument("--validate-record", type=Path, help="validate completed drill evidence JSON")
    args = parser.parse_args()
    try:
        policy = load_json(args.policy)
        result = drill_plan(policy) if args.plan else validate_record(
            load_json(args.validate_record), policy
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.plan or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
