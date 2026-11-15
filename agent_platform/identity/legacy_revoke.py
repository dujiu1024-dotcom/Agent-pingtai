"""Explicit, backup-first invalidation of legacy Console tokens."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

CONFIRMATION = "REVOKE-ALL-LEGACY-CONSOLE-TOKENS"


def invalidate_legacy_console_tokens(
    database_path: str | Path,
    backup_path: str | Path,
    *,
    confirmation: str,
) -> dict[str, Any]:
    """Back up the SQLite database, then clear every users.console_token value."""
    if confirmation != CONFIRMATION:
        raise ValueError("explicit confirmation string is required")

    source = Path(database_path).expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source == backup:
        raise ValueError("backup path must differ from source database")
    if backup.exists():
        raise FileExistsError(backup)

    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA busy_timeout=5000")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)")
        }
        if "console_token" not in columns:
            raise ValueError("legacy users table has no console_token column")

        backup_connection = sqlite3.connect(backup)
        try:
            connection.backup(backup_connection)
        finally:
            backup_connection.close()

        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE users SET console_token=NULL WHERE console_token IS NOT NULL"
            )
            revoked = max(cursor.rowcount, 0)
            remaining = connection.execute(
                "SELECT COUNT(*) FROM users WHERE console_token IS NOT NULL"
            ).fetchone()[0]
            if remaining:
                raise RuntimeError("legacy console token invalidation verification failed")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return {
        "source_database": source.name,
        "backup_database": str(backup),
        "revoked_token_count": revoked,
        "remaining_legacy_tokens": 0,
        "source_mutated": True,
        "confirmation": CONFIRMATION,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Back up and invalidate all legacy SQLite Console tokens."
    )
    parser.add_argument("--db", required=True, help="Path to legacy SQLite database")
    parser.add_argument("--backup", required=True, help="New backup file path; must not exist")
    parser.add_argument(
        "--confirm", required=True,
        help=f"Type exactly: {CONFIRMATION}",
    )
    args = parser.parse_args()
    print(invalidate_legacy_console_tokens(
        args.db, args.backup, confirmation=args.confirm,
    ))


if __name__ == "__main__":
    main()
