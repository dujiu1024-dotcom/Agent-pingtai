#!/usr/bin/env python3
"""Operator-assisted lifecycle checks and evidence validation; no production mutations."""
from __future__ import annotations

import argparse
import json
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = {
    "deploy": ["Review environment and secret injection", "Run docker compose config --quiet",
        "Apply reviewed migrations using the release-specific migration command",
        "Deploy the pinned image digest", "Wait for readiness and run verification"],
    "upgrade": ["Capture and verify a pre-upgrade backup", "Check release and schema compatibility",
        "Run the expand migration", "Roll out the pinned image gradually and monitor health",
        "Run post-upgrade verification before contract cleanup"],
    "backup": ["Record backup ID, UTC time, source release and retention",
        "Create PostgreSQL backup using the approved database procedure",
        "Capture Redis RDB/AOF according to its durability role",
        "Snapshot object data and versioned configuration",
        "Encrypt artifacts, calculate SHA-256 and verify readability",
        "Store manifest and evidence in restricted backup storage"],
    "restore": ["Declare incident and isolate writes/traffic",
        "Select a verified backup set and validate its manifest/checksums",
        "Provision isolated dependencies and restore configuration/secrets securely",
        "Restore PostgreSQL, Redis and object data in the approved order",
        "Reconcile references, Outbox/events and tenant authorization",
        "Run smoke tests; resume traffic gradually after approval"],
    "rollback": ["Declare rollback and freeze deployments/migrations",
        "Confirm previous image digest and schema compatibility",
        "Restore previous pinned image/configuration",
        "Run compensating migration only with explicit approval",
        "Verify health, integrity and background consumers before reopening traffic"],
    "verify": ["Check deployment and dependency health/readiness",
        "Run unit, contract and environment-appropriate integration tests",
        "Verify tenant isolation, authorization, queue lag and error/latency budgets",
        "Confirm backup freshness and recent restore-drill evidence"],
    "diagnose": ["Capture deployment version, UTC time, incident ID and affected scope",
        "Inspect health, recent deploys, dependency status and resource pressure",
        "Inspect sanitized logs, queue lag and database connection saturation",
        "Preserve evidence; redact tokens, credentials and customer payloads",
        "Escalate before destructive recovery, failover or data repair"],
}

def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest must be a JSON object")
    return value

def validate_backup_manifest(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("backup_id", "created_at", "source_release", "artifacts"):
        if not data.get(field):
            errors.append(f"missing required field: {field}")
    if data.get("created_at"):
        try:
            stamp = datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                errors.append("created_at must include a timezone")
        except ValueError:
            errors.append("created_at must be an ISO-8601 timestamp")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("artifacts must be a non-empty list")
    else:
        names: set[str] = set()
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, dict):
                errors.append(f"artifacts[{index}] must be an object")
                continue
            name, checksum, location = artifact.get("name"), artifact.get("sha256"), artifact.get("location")
            if not isinstance(name, str) or not name.strip() or name in names:
                errors.append(f"artifacts[{index}] has a missing or duplicate name")
            else:
                names.add(name)
            if not isinstance(checksum, str) or len(checksum) != 64 or any(
                char not in "0123456789abcdefABCDEF" for char in checksum
            ):
                errors.append(f"artifacts[{index}] sha256 must be 64 hexadecimal characters")
            if not isinstance(location, str) or not location.strip():
                errors.append(f"artifacts[{index}] location is required")
    components = data.get("components")
    if not isinstance(components, dict):
        components = {}
    for component in ("postgres", "redis", "objects", "configuration"):
        if components.get(component) is not True:
            errors.append(f"component backup missing or unverified: {component}")
    return errors

def preflight(root: Path) -> dict[str, Any]:
    env_file = root / ".env"
    env_mode = None
    warning = None
    if env_file.exists():
        mode = stat.S_IMODE(env_file.stat().st_mode)
        env_mode = oct(mode)
        if mode & 0o077:
            warning = ".env permissions are broader than 0600; run chmod 600 .env"
    compose = next((p for p in (root / "compose.yaml", root / "compose.yml",
        root / "docker-compose.yml") if p.is_file()), None)
    return {"python": sys.version.split()[0], "python_supported": sys.version_info >= (3, 11),
        "git_available": shutil.which("git") is not None, "docker_available": shutil.which("docker") is not None,
        "compose_file": str(compose.relative_to(root)) if compose else None,
        "env_file_present": env_file.is_file(), "env_file_mode": env_mode,
        "warnings": [warning] if warning else [], "secret_values_emitted": False}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="print a lifecycle checklist")
    plan.add_argument("operation", choices=tuple(OPERATIONS))
    sub.add_parser("preflight", help="inspect local prerequisites without changing state")
    manifest = sub.add_parser("validate-backup", help="validate a backup manifest")
    manifest.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = {"operation": args.operation, "steps": OPERATIONS[args.operation],
                "execution": "not_performed"}
            status = 0
        elif args.command == "preflight":
            result = preflight(ROOT)
            status = 0 if result["python_supported"] else 1
        else:
            errors = validate_backup_manifest(load_object(args.manifest))
            result = {"valid": not errors, "errors": errors}
            status = 0 if not errors else 1
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        result, status = {"valid": False, "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return status

if __name__ == "__main__":
    raise SystemExit(main())
