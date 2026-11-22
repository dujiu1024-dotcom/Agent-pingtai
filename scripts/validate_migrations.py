#!/usr/bin/env python3
"""Read-only migration filename and SHA-256 sidecar validator.

Each SQL migration must have a sibling checksum file named
"<migration>.sha256" containing a 64-character SHA-256 hex digest. The
validator never connects to a database and never writes to the migration tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

MIGRATION_NAME = re.compile(r"^(?P<number>\d{3})_[A-Za-z0-9_]+\.sql$")
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def validate_migrations(directory: Path) -> dict[str, Any]:
    """Return a deterministic report without modifying files or accessing a DB."""
    if directory.is_symlink():
        raise ValueError(f"migration directory must not be a symlink: {directory}")
    if not directory.exists() or not directory.is_dir():
        raise ValueError(f"migration directory does not exist: {directory}")

    files = sorted(directory.glob("*.sql"), key=lambda path: path.name)
    numbered: dict[int, list[str]] = defaultdict(list)
    issues: list[dict[str, str]] = []
    checksums: list[dict[str, str]] = []

    if not files:
        issues.append({"code": "no_migrations", "path": str(directory)})

    for migration in files:
        match = MIGRATION_NAME.fullmatch(migration.name)
        if match is None:
            issues.append({"code": "invalid_filename", "path": migration.name})
            continue
        number = int(match.group("number"))
        numbered[number].append(migration.name)

        if migration.is_symlink() or not migration.is_file():
            issues.append({"code": "invalid_migration_file", "path": migration.name})
            continue

        checksum_path = migration.with_name(f"{migration.name}.sha256")
        if checksum_path.is_symlink() or not checksum_path.is_file():
            issues.append({"code": "missing_checksum", "path": checksum_path.name})
            continue

        try:
            checksum_text = checksum_path.read_text(encoding="ascii").strip()
            expected = checksum_text.split()[0] if checksum_text else ""
            if not SHA256.fullmatch(expected):
                issues.append({"code": "invalid_checksum", "path": checksum_path.name})
                continue
            actual = hashlib.sha256(migration.read_bytes()).hexdigest()
        except (OSError, UnicodeError, IndexError) as exc:
            issues.append(
                {"code": "checksum_read_error", "path": checksum_path.name, "detail": str(exc)}
            )
            continue

        if expected.lower() != actual:
            issues.append({"code": "checksum_mismatch", "path": migration.name})
            continue
        checksums.append({"path": migration.name, "sha256": actual})

    for number, names in sorted(numbered.items()):
        if len(names) > 1:
            issues.append(
                {"code": "duplicate_number", "number": str(number), "paths": ", ".join(names)}
            )

    issues.sort(key=lambda issue: (issue["code"], issue.get("path", ""), issue.get("number", "")))
    return {
        "valid": not issues,
        "migration_count": len(files),
        "verified_checksum_count": len(checksums),
        "checksums": checksums,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path("agent_platform/storage/postgres"),
        help="directory containing SQL migrations",
    )
    args = parser.parse_args(argv)
    try:
        report = validate_migrations(args.directory)
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
