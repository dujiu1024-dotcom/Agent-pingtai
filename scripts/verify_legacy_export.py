#!/usr/bin/env python3
"""Verify a legacy SQLite JSONL export against its manifest without modifying it."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any


def verify_export(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "legacy-sqlite-jsonl-v1":
        raise ValueError("unsupported export manifest format")
    tables = manifest.get("tables")
    if not isinstance(tables, list):
        raise ValueError("manifest tables must be a list")
    names: set[str] = set()
    total_rows = 0
    results = []
    for table in tables:
        if not isinstance(table, dict):
            raise ValueError("invalid table manifest entry")
        name, filename = table.get("name"), table.get("file")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("table names must be unique non-empty strings")
        names.add(name)
        if not isinstance(filename, str):
            raise ValueError(f"missing export file for table {name}")
        relative = PurePosixPath(filename)
        if relative.is_absolute() or len(relative.parts) != 1 or relative.name in {"", ".", ".."}:
            raise ValueError(f"unsafe export filename for table {name}")
        path = output_dir / relative.name
        digest = hashlib.sha256()
        count = 0
        with path.open("rb") as stream:
            for raw in stream:
                digest.update(raw)
                try:
                    row = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"invalid JSONL row in {filename} at line {count + 1}") from exc
                if row.get("table") != name or not isinstance(row.get("row"), dict):
                    raise ValueError(f"row/table mismatch in {filename} at line {count + 1}")
                count += 1
        if digest.hexdigest() != table.get("sha256"):
            raise ValueError(f"checksum mismatch for {filename}")
        if count != table.get("rows_exported"):
            raise ValueError(f"row count mismatch for {filename}")
        total_rows += count
        results.append({"table": name, "rows": count, "sha256": digest.hexdigest()})
    expected_files = {manifest_path.name, *(item["file"] for item in tables)}
    actual_files = {item.name for item in output_dir.iterdir() if item.is_file()}
    unexpected = sorted(actual_files - expected_files)
    missing = sorted(expected_files - actual_files)
    if unexpected or missing:
        raise ValueError(f"export file set mismatch; unexpected={unexpected}, missing={missing}")
    return {"valid": True, "tables": len(tables), "rows": total_rows, "files": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    try:
        report = verify_export(args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
