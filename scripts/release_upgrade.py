#!/usr/bin/env python3
"""Fail-closed release upgrade planner with explicit rollback contracts.

This module plans and validates an upgrade; it never executes production changes.
The deployment system must execute the emitted steps and record their outcomes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: Any, field: str, errors: list[str]) -> "Version | None":
        if not isinstance(value, str) or not SEMVER_RE.fullmatch(value):
            errors.append(f"{field}: must be a semantic version (MAJOR.MINOR.PATCH)")
            return None
        core = value.split("-", 1)[0].split("+", 1)[0]
        return cls(*(int(part) for part in core.split(".")))


def validate_upgrade_manifest(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["manifest must be a JSON object"]
    required = ("release_id", "from_version", "to_version", "image_digest",
                "config_revision", "previous_config_revision", "compatibility",
                "migration", "rollout", "rollback")
    for field in required:
        if field not in document:
            errors.append(f"{field}: required")
    if errors:
        return errors
    for field in ("release_id", "image_digest", "config_revision", "previous_config_revision"):
        if not isinstance(document[field], str) or not document[field].strip():
            errors.append(f"{field}: must be a non-empty string")
    if not isinstance(document["image_digest"], str) or not IMAGE_DIGEST_RE.fullmatch(document["image_digest"]):
        errors.append("image_digest: must use an immutable sha256 image digest")
    old = Version.parse(document["from_version"], "from_version", errors)
    new = Version.parse(document["to_version"], "to_version", errors)
    if old and new and new <= old:
        errors.append("to_version: must be greater than from_version")
    compatibility = document["compatibility"]
    if not isinstance(compatibility, dict):
        errors.append("compatibility: must be an object")
    else:
        for field in ("min_app_version", "max_app_version", "min_schema_version", "max_schema_version"):
            if not isinstance(compatibility.get(field), str) or not compatibility[field].strip():
                errors.append(f"compatibility.{field}: required")
        if compatibility.get("window_minutes") is None or type(compatibility.get("window_minutes")) is not int or compatibility["window_minutes"] < 1:
            errors.append("compatibility.window_minutes: must be a positive integer")
        if compatibility.get("old_and_new_coexist") is not True:
            errors.append("compatibility.old_and_new_coexist: must be true for rolling upgrade")
        if compatibility.get("min_app_version") and compatibility.get("max_app_version"):
            lo = Version.parse(compatibility["min_app_version"], "compatibility.min_app_version", errors)
            hi = Version.parse(compatibility["max_app_version"], "compatibility.max_app_version", errors)
            if lo and hi and lo > hi:
                errors.append("compatibility: minimum app version exceeds maximum")
    migration = document["migration"]
    if not isinstance(migration, dict):
        errors.append("migration: must be an object")
    else:
        if migration.get("preflight_passed") is not True:
            errors.append("migration.preflight_passed: must be true")
        if migration.get("backup_ref") is None or not str(migration.get("backup_ref", "")).strip():
            errors.append("migration.backup_ref: required verified backup reference")
        if migration.get("expand_contract") is not True:
            errors.append("migration.expand_contract: must be true")
        if migration.get("destructive") is True and migration.get("contract_after_compatibility_window") is not True:
            errors.append("migration: destructive contract must wait until compatibility window closes")
        if migration.get("rollback_strategy") not in ("none", "compensating_migration", "restore_backup"):
            errors.append("migration.rollback_strategy: unsupported strategy")
        if migration.get("rollback_strategy") == "compensating_migration" and not migration.get("compensating_migration_ref"):
            errors.append("migration.compensating_migration_ref: required")
        if migration.get("rollback_strategy") == "restore_backup" and not migration.get("restore_drill_ref"):
            errors.append("migration.restore_drill_ref: required successful restore drill")
    rollout = document["rollout"]
    if not isinstance(rollout, dict):
        errors.append("rollout: must be an object")
    else:
        batches = rollout.get("batches")
        if not isinstance(batches, list) or not batches or any(type(x) is not int or x < 1 or x > 100 for x in batches):
            errors.append("rollout.batches: must be non-empty integer percentages in [1, 100]")
        elif sum(batches) != 100:
            errors.append("rollout.batches: percentages must sum to 100")
        if rollout.get("health_gate") is not True:
            errors.append("rollout.health_gate: must be enabled")
        if rollout.get("pause_minutes") is None or type(rollout.get("pause_minutes")) is not int or rollout["pause_minutes"] < 0:
            errors.append("rollout.pause_minutes: must be a non-negative integer")
    rollback = document["rollback"]
    if not isinstance(rollback, dict):
        errors.append("rollback: must be an object")
    else:
        for field in ("previous_image_digest", "previous_config_revision"):
            if not isinstance(rollback.get(field), str) or not rollback[field].strip():
                errors.append(f"rollback.{field}: required")
        if isinstance(rollback.get("previous_image_digest"), str) and not IMAGE_DIGEST_RE.fullmatch(rollback["previous_image_digest"]):
            errors.append("rollback.previous_image_digest: must use an immutable sha256 image digest")
        if rollback.get("config_restore_ref") is None or not str(rollback.get("config_restore_ref", "")).strip():
            errors.append("rollback.config_restore_ref: required immutable config snapshot")
        if rollback.get("automatic_on_health_failure") is not True:
            errors.append("rollback.automatic_on_health_failure: must be true")
    if isinstance(rollback, dict) and rollback.get("previous_config_revision") != document.get("previous_config_revision"):
        errors.append("rollback.previous_config_revision: does not match manifest previous_config_revision")
    return errors


def build_plan(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ordered steps; callers must execute only after validation succeeds."""
    rollout = document["rollout"]
    migration = document["migration"]
    steps: list[dict[str, Any]] = [
        {"step": "freeze_and_snapshot", "config_snapshot": document["previous_config_revision"],
         "backup_ref": migration["backup_ref"]},
        {"step": "migration_preflight", "required": ["schema_drift", "lock_risk", "disk_capacity",
         "replication_lag", "backup_restore_evidence", "pending_jobs", "compatibility_matrix"]},
        {"step": "expand_migration", "migration_ref": migration.get("migration_ref"),
         "strategy": "expand_contract"},
        {"step": "compatibility_window", "minutes": document["compatibility"]["window_minutes"],
         "old_and_new_coexist": True},
    ]
    for index, percentage in enumerate(rollout["batches"], start=1):
        steps.append({"step": "rolling_batch", "batch": index, "traffic_percent": percentage,
                      "health_gate": True, "pause_minutes": rollout["pause_minutes"]})
    steps.extend([
        {"step": "commit_release", "image_digest": document["image_digest"],
         "config_revision": document["config_revision"]},
        {"step": "defer_contract_migration",
         "required_after_compatibility_window": migration.get("contract_after_compatibility_window", False)},
    ])
    return steps


def build_rollback(document: dict[str, Any]) -> list[dict[str, Any]]:
    migration = document["migration"]
    actions = [
        {"step": "stop_rollout_and_drain_new_version"},
        {"step": "restore_application_image", "image_digest": document["rollback"]["previous_image_digest"]},
        {"step": "restore_configuration", "config_revision": document["rollback"]["previous_config_revision"],
         "snapshot_ref": document["rollback"]["config_restore_ref"]},
    ]
    strategy = migration["rollback_strategy"]
    if strategy == "compensating_migration":
        actions.append({"step": "apply_compensating_migration",
                        "migration_ref": migration["compensating_migration_ref"]})
    elif strategy == "restore_backup":
        actions.append({"step": "restore_database_backup",
                        "backup_ref": migration["backup_ref"],
                        "restore_drill_ref": migration["restore_drill_ref"]})
    else:
        actions.append({"step": "preserve_backward_compatible_schema",
                        "note": "No automatic destructive schema rollback; use forward fix or approved restore"})
    actions.append({"step": "verify_previous_version_health"})
    return actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"UPGRADE GATE: BLOCKED: cannot read manifest: {exc}", file=sys.stderr)
        return 2
    errors = validate_upgrade_manifest(document)
    if errors:
        print("UPGRADE GATE: BLOCKED")
        for error in errors:
            print(f"- {error}")
        return 1
    result = {"release_id": document["release_id"], "upgrade_plan": build_plan(document),
              "rollback_plan": build_rollback(document),
              "execution": "not_performed; plans require an approved deployment executor"}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"UPGRADE GATE: READY ({document['release_id']})")
        for item in result["upgrade_plan"]:
            print(f"- {item['step']}")
        print("Rollback:")
        for item in result["rollback_plan"]:
            print(f"- {item['step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
