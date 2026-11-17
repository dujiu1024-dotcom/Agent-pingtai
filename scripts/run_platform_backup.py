"""Run all platform backup components and verify each newly created artifact.

The runner does not decrypt or restore backups. It requires a fresh artifact
from every component and validates its checksum sidecar and manifest before
reporting success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

try:
    from scripts.verify_platform_backups import component_for, verify_artifact
except ModuleNotFoundError:
    # Direct execution puts the scripts directory, rather than the repository
    # root, on sys.path.
    from verify_platform_backups import component_for, verify_artifact

ROOT = Path(__file__).resolve().parents[1]
_COMPONENTS = (
    ("postgres", "postgres-custom-age-v1", "backup_postgres.sh"),
    ("redis", "redis-rdb-age-v1", "backup_redis.sh"),
    ("object-storage", "minio-mirror-age-v1", "backup_object_storage.sh"),
)
Runner = Callable[..., Any]


def _artifacts_for(directory: Path, expected_format: str) -> set[Path]:
    return {
        path for path in directory.glob("*.age")
        if component_for(path) == expected_format
    }


def run_backup_set(
    backup_dir: Path,
    *,
    root: Path = ROOT,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Run and verify a fresh artifact for PostgreSQL, Redis and object storage."""
    backup_dir = backup_dir.expanduser()
    if backup_dir.is_symlink():
        raise ValueError("backup directory must not be a symlink")
    backup_dir.mkdir(parents=True, exist_ok=True)
    if not backup_dir.is_dir():
        raise ValueError("backup directory must be a directory")
    backup_dir.chmod(0o700)

    verified: list[dict[str, str]] = []
    for component, expected_format, script_name in _COMPONENTS:
        script = root / "scripts" / script_name
        if script.is_symlink() or not script.is_file():
            raise ValueError(f"{component}: backup script is missing or unsafe")
        before = _artifacts_for(backup_dir, expected_format)
        environment = os.environ.copy()
        environment["BACKUP_DIR"] = str(backup_dir.resolve())
        try:
            runner(["bash", str(script)], check=True, env=environment)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"{component}: backup command failed with exit code {exc.returncode}"
            ) from exc

        created = sorted(_artifacts_for(backup_dir, expected_format) - before)
        if not created:
            raise RuntimeError(f"{component}: backup command produced no new recognized artifact")
        for artifact in created:
            errors = verify_artifact(artifact)
            if errors:
                raise RuntimeError(f"{component}: artifact verification failed: {'; '.join(errors)}")
            verified.append({
                "component": component,
                "artifact": artifact.name,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            })

    return {
        "status": "verified",
        "components": [item[0] for item in _COMPONENTS],
        "verified_artifacts": verified,
        "execution": "backup commands executed; restore not performed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"))
    args = parser.parse_args(argv)
    try:
        result = run_backup_set(args.backup_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
