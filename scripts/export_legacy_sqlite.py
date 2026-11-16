#!/usr/bin/env python3
"""Read-only, deterministic SQLite inventory and JSONL export for staged migrations."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

_SENSITIVE = re.compile(r"(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)", re.I)


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$binary_base64": base64.b64encode(value).decode("ascii")}
    return value


def export_database(database: Path, output_dir: Path, tables: list[str] | None = None) -> dict[str, Any]:
    if not database.is_file():
        raise FileNotFoundError(database)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be empty: {output_dir}")

    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    manifest: dict[str, Any] = {
        "format": "legacy-sqlite-jsonl-v1",
        "source": database.name,
        "read_only": True,
        "sensitive_columns": "redacted by column-name heuristic; review nested JSON/text separately",
        "tables": [],
    }
    try:
        connection.execute("BEGIN")
        available = [
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        selected = sorted(set(tables)) if tables else available
        unknown = set(selected) - set(available)
        if unknown:
            raise ValueError("unknown SQLite tables: " + ", ".join(sorted(unknown)))

        for table in selected:
            columns = list(connection.execute(f"PRAGMA table_info({_identifier(table)})"))
            names = [column["name"] for column in columns]
            primary = [column["name"] for column in sorted(columns, key=lambda c: c["pk"]) if column["pk"]]
            sensitive = {name for name in names if _SENSITIVE.search(name)}
            order = primary or names
            query = f"SELECT * FROM {_identifier(table)}"
            if order:
                query += " ORDER BY " + ",".join(_identifier(name) for name in order)
            destination = output_dir / (re.sub(r"[^A-Za-z0-9_.-]", "_", table) + ".jsonl")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            count = 0
            digest = hashlib.sha256()
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                for source_row in connection.execute(query):
                    row = {name: ("[REDACTED]" if name in sensitive else _json_value(source_row[name]))
                           for name in names}
                    record = {
                        "table": table,
                        "primary_key": {name: row[name] for name in primary} if primary else None,
                        "row": row,
                    }
                    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"), default=str) + "\n"
                    stream.write(encoded)
                    digest.update(encoded.encode("utf-8"))
                    count += 1
            temporary.replace(destination)
            manifest["tables"].append({
                "name": table,
                "columns": [
                    {"name": c["name"], "type": c["type"], "notnull": bool(c["notnull"]),
                     "primary_key_order": c["pk"]}
                    for c in columns
                ],
                "primary_key": primary,
                "rows_exported": count,
                "redacted_columns": sorted(sensitive),
                "file": destination.name,
                "sha256": digest.hexdigest(),
            })
        connection.rollback()
    finally:
        connection.close()

    manifest_path = output_dir / "manifest.json"
    temporary_manifest = output_dir / "manifest.json.tmp"
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="legacy SQLite database (opened read-only)")
    parser.add_argument("output_dir", type=Path, help="new or empty directory for JSONL export")
    parser.add_argument("--table", action="append", dest="tables",
                        help="export only this table; may be repeated")
    args = parser.parse_args()
    try:
        manifest = export_database(args.database, args.output_dir, args.tables)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "tables": len(manifest["tables"]),
        "rows": sum(item["rows_exported"] for item in manifest["tables"]),
        "manifest": str(args.output_dir / "manifest.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
