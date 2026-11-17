#!/usr/bin/env python3
"""Safe operational preflight and backup-artifact verification helpers.

This tool is intentionally read-only. It never deploys, restores, deletes, or
changes traffic; those actions remain explicit operator-controlled procedures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_SHA256_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")


class OpsCheckError(RuntimeError):
    """Raised when an operational safety check fails."""


def verify_backup(artifact: Path) -> dict[str, Any]:
    """Verify artifact, sha256 sidecar and JSON manifest as one published set."""
    artifact = artifact.expanduser()
    if not artifact.is_file() or artifact.is_symlink():
        raise OpsCheckError(f"backup artifact is missing or not a regular file: {artifact}")
    checksum_path = Path(f"{artifact}.sha256")
    manifest_path = Path(f"{artifact}.manifest.json")
    for required in (checksum_path, manifest_path):
        if not required.is_file() or required.is_symlink():
            raise OpsCheckError(f"required backup metadata is missing or unsafe: {required}")

    checksum_text = checksum_path.read_text(encoding="utf-8").strip()
    match = _SHA256_LINE.fullmatch(checksum_text)
    if not match:
        raise OpsCheckError("checksum sidecar must contain one sha256sum-compatible line")
    expected_sidecar, sidecar_name = match.groups()
    if sidecar_name != artifact.name:
        raise OpsCheckError("checksum sidecar filename does not match backup artifact")

    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected_sidecar.lower():
        raise OpsCheckError("backup artifact checksum does not match sidecar")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpsCheckError(f"backup manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise OpsCheckError("backup manifest must be a JSON object")
    if manifest.get("artifact") != artifact.name:
        raise OpsCheckError("manifest artifact name does not match backup")
    if str(manifest.get("sha256", "")).lower() != actual:
        raise OpsCheckError("manifest digest does not match backup artifact")
    if not manifest.get("format"):
        raise OpsCheckError("manifest format is missing")

    return {
        "artifact": str(artifact),
        "format": manifest["format"],
        "sha256": actual,
        "created_at_utc": manifest.get("created_at_utc"),
        "verified": True,
    }


def check_commands(commands: list[str]) -> list[str]:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise OpsCheckError("required commands are unavailable: " + ", ".join(missing))
    return commands


def check_env_file(path: Path, required_names: list[str]) -> list[str]:
    """Check variable names without evaluating or printing secret values."""
    if not path.is_file() or path.is_symlink():
        raise OpsCheckError(f"environment file is missing or unsafe: {path}")
    present: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            present.add(name)
    missing = sorted(set(required_names) - present)
    if missing:
        raise OpsCheckError("required environment variable names are missing: " + ", ".join(missing))
    return sorted(set(required_names))


def verify_health(url: str, timeout: float = 5.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "agent-platform-ops-check/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise OpsCheckError(f"health endpoint request failed: {exc}") from exc
    if not 200 <= status < 400:
        raise OpsCheckError(f"health endpoint returned HTTP {status}")
    return {"url": url, "http_status": status, "healthy": True}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="check host tools and optional env names")
    preflight.add_argument("--require-command", action="append", default=[])
    preflight.add_argument("--env-file", type=Path)
    preflight.add_argument("--require-env", action="append", default=[])

    backup = sub.add_parser("verify-backup", help="verify backup + checksum + manifest")
    backup.add_argument("artifact", type=Path)

    health = sub.add_parser("verify-health", help="perform a read-only HTTP health check")
    health.add_argument("--url", required=True)
    health.add_argument("--timeout", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result: dict[str, Any] = {"commands": check_commands(args.require_command)}
            if args.env_file:
                result["required_env_names"] = check_env_file(args.env_file, args.require_env)
            elif args.require_env:
                raise OpsCheckError("--require-env requires --env-file")
        elif args.command == "verify-backup":
            result = verify_backup(args.artifact)
        else:
            if args.timeout <= 0:
                raise OpsCheckError("--timeout must be greater than zero")
            result = verify_health(args.url, args.timeout)
    except (OpsCheckError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
