#!/usr/bin/env python3
"""Verify backup payload, checksum sidecar, manifest and optional decrypted format.

Without --decrypt this validates the encrypted artifact's SHA-256 chain. With
--decrypt it also authenticates age ciphertext and checks the native payload
format. It never restores data or changes a service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

SIDECAR_RE = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)\n?$")
EXPECTED_FORMATS = {
    "platform-config-": "platform-config-tar-age-v1",
    "platform-": "postgres-custom-age-v1",
    "redis-": "redis-rdb-age-v1",
    "object-storage-": "minio-mirror-age-v1",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def component_for(path: Path) -> str | None:
    for prefix, fmt in EXPECTED_FORMATS.items():
        if path.name.startswith(prefix):
            return fmt
    return None


def validate_tar(path: Path, *, expected_root: str | None = None) -> None:
    seen: set[str] = set()
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            name = member.name
            item = PurePosixPath(name)
            if not name or item.is_absolute() or ".." in item.parts or "\\" in name:
                raise ValueError("archive contains an unsafe path")
            if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise ValueError("archive contains an unsupported filesystem entry")
            if name in seen:
                raise ValueError("archive contains duplicate paths")
            seen.add(name)
            if expected_root and item.parts[0] != expected_root:
                raise ValueError("object archive contains an unexpected bucket root")
        if not seen:
            raise ValueError("archive is empty")


def validate_decrypted(artifact: Path, manifest: dict[str, Any], identity: str | None) -> None:
    if not identity:
        raise ValueError("AGE_IDENTITY is required with --decrypt")
    with tempfile.TemporaryDirectory(prefix="platform-backup-verify-") as directory:
        plaintext = Path(directory) / "payload"
        subprocess.run(
            ["age", "--decrypt", "--identity", identity, "--output", str(plaintext), str(artifact)],
            check=True, capture_output=True, text=True,
        )
        fmt = manifest["format"]
        if fmt == "postgres-custom-age-v1":
            subprocess.run(["pg_restore", "--list", str(plaintext)], check=True, capture_output=True, text=True)
        elif fmt == "redis-rdb-age-v1":
            subprocess.run(["redis-check-rdb", str(plaintext)], check=True, capture_output=True, text=True)
        elif fmt == "minio-mirror-age-v1":
            validate_tar(plaintext, expected_root=manifest.get("bucket"))
        elif fmt == "platform-config-tar-age-v1":
            validate_tar(plaintext)
        else:
            raise ValueError(f"unsupported backup format: {fmt}")


def verify_artifact(artifact: Path, *, decrypt: bool = False, identity: str | None = None) -> list[str]:
    errors: list[str] = []
    if artifact.is_symlink() or not artifact.is_file():
        return [f"{artifact.name}: artifact must be a regular non-symlink file"]
    fmt = component_for(artifact)
    if fmt is None:
        return [f"{artifact.name}: unknown backup artifact prefix"]
    checksum = Path(str(artifact) + ".sha256")
    manifest_path = Path(str(artifact) + ".manifest.json")
    for sidecar in (checksum, manifest_path):
        if sidecar.is_symlink() or not sidecar.is_file():
            errors.append(f"{artifact.name}: missing or unsafe {sidecar.name}")
    if errors:
        return errors
    try:
        sidecar_text = checksum.read_text(encoding="utf-8")
        match = SIDECAR_RE.fullmatch(sidecar_text)
        if not match or match.group(2) != artifact.name:
            errors.append(f"{artifact.name}: malformed or mismatched checksum sidecar")
        digest = sha256_file(artifact)
        if match and match.group(1) != digest:
            errors.append(f"{artifact.name}: checksum sidecar digest mismatch")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != fmt:
            errors.append(f"{artifact.name}: manifest format mismatch")
        if manifest.get("artifact") != artifact.name:
            errors.append(f"{artifact.name}: manifest artifact name mismatch")
        if manifest.get("sha256") != digest:
            errors.append(f"{artifact.name}: manifest digest mismatch")
        if not errors and decrypt:
            validate_decrypted(artifact, manifest, identity)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        errors.append(f"{artifact.name}: verification failed: {exc}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"))
    parser.add_argument("--decrypt", action="store_true", help="also authenticate and inspect decrypted payloads")
    parser.add_argument("--age-identity", default=os.environ.get("AGE_IDENTITY"))
    args = parser.parse_args(argv)
    root = args.backup_dir
    if root.is_symlink() or not root.is_dir():
        print(f"BACKUP VERIFY: BLOCKED: invalid backup directory: {root}", file=sys.stderr)
        return 2
    if args.decrypt and not args.age_identity:
        print("BACKUP VERIFY: BLOCKED: --decrypt requires --age-identity or AGE_IDENTITY", file=sys.stderr)
        return 2
    artifacts = sorted(path for path in root.glob("*.age") if component_for(path))
    if not artifacts:
        print("BACKUP VERIFY: BLOCKED: no recognized backup artifacts found", file=sys.stderr)
        return 1
    failures = 0
    for artifact in artifacts:
        errors = verify_artifact(artifact, decrypt=args.decrypt, identity=args.age_identity)
        if errors:
            failures += 1
            for error in errors:
                print(f"FAIL: {error}")
        else:
            suffix = " (decrypted format checked)" if args.decrypt else ""
            print(f"OK: {artifact.name}{suffix}")
    print(f"BACKUP VERIFY: {'FAIL' if failures else 'PASS'}; checked={len(artifacts)}; failed={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
