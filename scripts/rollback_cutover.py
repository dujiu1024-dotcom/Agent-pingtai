#!/usr/bin/env python3
"""Fail-closed operator orchestrator for legacy cutover rollback."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_STEPS = (
    "freeze_writes",
    "reverse_sync",
    "verify_parity",
    "switch_gateway_to_legacy",
    "verify_legacy_health",
    "unfreeze_legacy_writes",
)
MAX_ROLLBACK_SECONDS = 300


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_plan(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("plan must be an existing regular non-symlink file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("plan version must be 1")
    max_seconds = payload.get("max_duration_seconds", 270)
    if (isinstance(max_seconds, bool) or not isinstance(max_seconds, int)
            or not 1 <= max_seconds <= MAX_ROLLBACK_SECONDS):
        raise ValueError("max_duration_seconds must be between 1 and 300")
    steps = payload.get("steps")
    if not isinstance(steps, list) or len(steps) != len(REQUIRED_STEPS):
        raise ValueError("plan must define exactly six rollback steps")
    names: list[str] = []
    normalized: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        name, argv = step.get("name"), step.get("argv")
        timeout = step.get("timeout_seconds", 60)
        if not isinstance(name, str) or name not in REQUIRED_STEPS:
            raise ValueError("step name is not a recognized rollback phase")
        if not isinstance(argv, list) or not argv or any(
            not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv
        ):
            raise ValueError(f"{name}: argv must be a non-empty string array")
        executable = Path(argv[0])
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f"{name}: executable must be an existing absolute executable path")
        if (isinstance(timeout, bool) or not isinstance(timeout, int)
                or not 1 <= timeout <= max_seconds):
            raise ValueError(f"{name}: timeout_seconds must be between 1 and max_duration_seconds")
        names.append(name)
        normalized.append({"name": name, "argv": argv, "timeout_seconds": timeout})
    if tuple(names) != REQUIRED_STEPS:
        raise ValueError("rollback steps must be in the required safety order")
    return {"version": 1, "max_duration_seconds": max_seconds, "steps": normalized}


def _write_evidence(path: Path, report: dict[str, Any]) -> None:
    if path.is_symlink() or path.exists():
        raise ValueError("evidence path must be new and must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary_name = stream.name
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def run_plan(plan: dict[str, Any], evidence_path: Path) -> int:
    started_at = _utc_now()
    started = time.monotonic()
    deadline = started + plan["max_duration_seconds"]
    results: list[dict[str, Any]] = []
    final_status = "succeeded"
    for step in plan["steps"]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            final_status = "deadline_exceeded"
            break
        step_started = time.monotonic()
        result: dict[str, Any] = {"name": step["name"], "started_at": _utc_now()}
        try:
            completed = subprocess.run(
                step["argv"], shell=False, check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, timeout=min(step["timeout_seconds"], remaining),
            )
            result["exit_code"] = completed.returncode
            result["status"] = "succeeded" if completed.returncode == 0 else "failed"
            if completed.returncode != 0:
                final_status = "step_failed"
        except subprocess.TimeoutExpired:
            result.update({"exit_code": None, "status": "timed_out"})
            final_status = "step_timed_out"
        except OSError as exc:
            result.update({"exit_code": None, "status": "launch_failed",
                           "error_type": type(exc).__name__})
            final_status = "step_launch_failed"
        result["duration_seconds"] = round(time.monotonic() - step_started, 3)
        result["finished_at"] = _utc_now()
        results.append(result)
        if result["status"] != "succeeded":
            break

    report = {
        "schema": "agent-platform-cutover-rollback-evidence-v1",
        "status": final_status,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "max_duration_seconds": plan["max_duration_seconds"],
        "completed_steps": len(results),
        "required_steps": list(REQUIRED_STEPS),
        "steps": results,
        "safety_note": (
            "On failure, later steps are not run. In particular, write freeze is "
            "not automatically lifted after a failed reverse-sync, parity, route "
            "switch or legacy-health check. Operator intervention is required."
        ),
    }
    _write_evidence(evidence_path, report)
    print(json.dumps({
        "status": final_status, "completed_steps": len(results),
        "duration_seconds": report["duration_seconds"],
        "evidence": str(evidence_path),
    }, ensure_ascii=False))
    return 0 if final_status == "succeeded" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--execute", action="store_true",
                        help="execute reviewed commands; without this flag only print the plan")
    parser.add_argument("--evidence", type=Path,
                        help="new evidence JSON path (required with --execute)")
    args = parser.parse_args()
    try:
        plan = load_plan(args.plan)
        if not args.execute:
            print(json.dumps({
                "mode": "plan_only", "max_duration_seconds": plan["max_duration_seconds"],
                "steps": [{"name": step["name"], "executable": Path(step["argv"][0]).name}
                          for step in plan["steps"]],
            }, ensure_ascii=False, indent=2))
            return 0
        if args.evidence is None:
            raise ValueError("--evidence is required with --execute")
        return run_plan(plan, args.evidence)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
