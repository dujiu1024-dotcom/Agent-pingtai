"""Read-only inventory of legacy SQLite console users for identity migration.

This module never updates the source database and never exports raw console tokens.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_COLUMNS = {
    "open_id", "name", "role", "projects", "console_token", "created_at",
}


def audit_legacy_users(database_path: str | Path) -> dict[str, Any]:
    """Read the legacy users table in SQLite read-only mode and build a mapping report."""
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    uri = f"{path.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "users" not in tables:
            raise ValueError("legacy database does not contain a users table")

        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(
                "legacy users table is missing required columns: " + ", ".join(missing)
            )

        rows = connection.execute(
            """SELECT open_id, name, role, projects, console_token, created_at
               FROM users ORDER BY created_at, open_id"""
        ).fetchall()

    role_counts: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    missing_identity = 0
    missing_role = 0
    token_count = 0
    project_count = 0

    for row in rows:
        open_id = str(row["open_id"] or "").strip()
        role = str(row["role"] or "").strip()
        project_ids = list(dict.fromkeys(
            item.strip()
            for item in str(row["projects"] or "").split(",")
            if item.strip()
        ))
        has_token = bool(str(row["console_token"] or ""))
        role_counts[role or "(missing)"] += 1
        missing_identity += int(not open_id)
        missing_role += int(not role)
        token_count += int(has_token)
        project_count += len(project_ids)

        records.append({
            "legacy_subject_ref": (
                hashlib.sha256(open_id.encode("utf-8")).hexdigest()[:16]
                if open_id else None
            ),
            "display_name": str(row["name"] or "").strip(),
            "legacy_role": role or None,
            "project_ids": project_ids,
            "created_at": row["created_at"],
            "legacy_console_token_present": has_token,
            "legacy_console_token_must_be_invalidated": has_token,
            "identity_provider_confirmation_required": True,
            "role_mapping_required": True,
            "account_claim_required": True,
        })

    return {
        "report_version": 1,
        "source_database": path.name,
        "source_mode": "read_only",
        "source_mutated": False,
        "raw_open_id_exported": False,
        "raw_console_token_exported": False,
        "summary": {
            "users_total": len(records),
            "legacy_role_counts": dict(sorted(role_counts.items())),
            "project_memberships_total": project_count,
            "users_with_legacy_console_token": token_count,
            "users_missing_open_id": missing_identity,
            "users_missing_role": missing_role,
            "identity_provider_confirmation_required": len(records),
            "manual_role_mapping_required": len(records),
            "account_claim_required": len(records),
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a read-only migration inventory for legacy SQLite users."
    )
    parser.add_argument("--db", required=True, help="Path to the legacy SQLite database")
    parser.add_argument("--output", help="Optional JSON report output path")
    args = parser.parse_args()

    report = audit_legacy_users(args.db)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).expanduser()
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
