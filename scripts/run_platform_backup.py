"""Run a verified, four-component platform backup set.

The runner requires fresh PostgreSQL, Redis, object-storage and encrypted
configuration artifacts. It verifies every artifact and emits a set manifest
that records component timestamps, checksums and snapshot skew. It does not
claim an atomic distributed snapshot or perform a restore.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

try:
    from scripts.verify_platform_backups import component_for, verify_artifact
except ModuleNotFoundError:
    from verify_platform_backups import component_for, verify_artifact

ROOT = Path(__file__).resolve().parents[1]
_COMPONENTS = (
    ("postgres", "postgres-custom-age-v1", "backup_postgres.sh"),
    ("redis", "redis-rdb-age-v1", "backup_redis.sh"),
    ("object-storage", "minio-mirror-age-v1", "backup_object_storage.sh"),
    ("config", "platform-config-tar-age-v1", "backup_platform_config.sh"),
)
Runner = Callable[..., Any]


def _artifacts_for(directory: Path, expected_format: str) -> set[Path]:
    return {
        path for path in directory.glob("*.age")
        if component_for(path) == expected_format
    }


def _parse_created_at(value: Any, *, artifact: Path) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{artifact.name}: manifest created_at_utc is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"{artifact.name}: invalid created_at_utc") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"{artifact.name}: created_at_utc must include timezone")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_backup_set(
    backup_dir: Path,
    *,
    root: Path = ROOT,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Run and verify fresh artifacts for every required platform component."""
    backup_dir = backup_dir.expanduser()
    if backup_dir.is_symlink():
        raise ValueError("backup directory must not be a symlink")
    backup_dir.mkdir(parents=True, exist_ok=True)
    if not backup_dir.is_dir():
        raise ValueError("backup directory must be a directory")
    backup_dir.chmod(0o700)

    verified: list[dict[str, str]] = []
    timestamps: list[datetime] = []
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
            manifest_path = Path(str(artifact) + ".manifest.json")
            component_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            created_at = _parse_created_at(component_manifest.get("created_at_utc"), artifact=artifact)
            timestamps.append(created_at)
            verified.append({
                "component": component,
                "artifact": artifact.name,
                "sha256": _sha256(artifact),
                "manifest_sha256": _sha256(manifest_path),
                "created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
            })

    policy_path = root / "deploy" / "disaster-recovery-policy.json"
    max_skew = 86400
    if policy_path.is_file() and not policy_path.is_symlink():
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        max_skew = int(policy.get("max_component_snapshot_skew_seconds", max_skew))
    if max_skew < 0:
        raise ValueError("max_component_snapshot_skew_seconds cannot be negative")
    skew = (max(timestamps) - min(timestamps)).total_seconds()
    if skew > max_skew:
        raise RuntimeError(
            f"cross-component snapshot skew {skew:.3f}s exceeds policy {max_skew}s"
        )

    backup_set_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = {
        "schema_version": 1,
        "backup_set_id": backup_set_id,
        "created_at_utc": created_at,
        "status": "verified",
        "components": verified,
        "component_count": len(verified),
        "snapshot_skew_seconds": round(skew, 3),
        "max_snapshot_skew_seconds": max_skew,
        "consistency": "independent component snapshots; not an atomic distributed snapshot",
        "restore_boundary": (
            "Verify this manifest and every artifact before restore. Restore PostgreSQL, "
            "Redis and objects in the documented order; apply config/secrets through "
            "the approved secret-management procedure."
        ),
    }
    manifest_path = backup_dir / f"platform-backup-set-{backup_set_id}.json"
    temporary = backup_dir / f".{manifest_path.name}.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(manifest_path)

    return {
        "status": "verified",
        "backup_set_id": backup_set_id,
        "components": [item[0] for item in _COMPONENTS],
        "verified_artifacts": verified,
        "snapshot_skew_seconds": round(skew, 3),
        "max_snapshot_skew_seconds": max_skew,
        "manifest": str(manifest_path),
        "execution": "four component backup commands executed; restore not performed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"))
    args = parser.parse_args(argv)
    try:
        result = run_backup_set(args.backup_dir)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
