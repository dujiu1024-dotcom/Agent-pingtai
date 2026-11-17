#!/usr/bin/env python3
"""Verify zero-difference parity between a SQLite export and PostgreSQL snapshots.

Target snapshots are independent, read-only JSONL exports with one JSON object
per row. Reports contain counts and SHA-256 digests, never row payloads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from scripts.migrate_sqlite_to_pg import _decode, load_export, load_mapping


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _row_key(row: dict[str, Any], columns: list[str], label: str) -> str:
    values = [row.get(column) for column in columns]
    if any(value is None for value in values):
        raise ValueError(f"{label}: primary key contains a null value")
    return _canonical(values)


def _index_rows(rows: list[dict[str, Any]], primary_key: list[str], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for line_number, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"{label}:{line_number}: each row must be a JSON object")
        key = _row_key(row, primary_key, label)
        if key in indexed:
            raise ValueError(f"{label}:{line_number}: duplicate primary key")
        indexed[key] = row
    return indexed


def _read_target(path: Path, expected_columns: set[str]) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"target snapshot must be a regular non-symlink file: {path.name}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_number}: invalid JSONL") from exc
            if not isinstance(value, dict) or set(value) != expected_columns:
                raise ValueError(f"{path.name}:{line_number}: target columns differ from mapping")
            rows.append(_decode(value))
    return rows


def _checksum(rows: dict[str, dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for key in sorted(rows):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical(rows[key]).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def verify_parity(export_dir: Path, target_dir: Path, mapping_path: Path) -> dict[str, Any]:
    if target_dir.is_symlink() or not target_dir.is_dir():
        raise ValueError("target snapshot directory must be a real directory")
    manifest, source_by_table = load_export(export_dir)
    mappings = load_mapping(mapping_path, manifest)

    expected_source_files = {"manifest.json"} | {item["file"] for item in manifest["tables"]}
    actual_source_files = {item.name for item in export_dir.iterdir()}
    if actual_source_files != expected_source_files:
        missing = sorted(expected_source_files - actual_source_files)
        extra = sorted(actual_source_files - expected_source_files)
        raise ValueError(
            "source export inventory mismatch "
            f"(missing={missing}, extra={extra})"
        )

    expected_files = {f"{item['target_schema']}__{item['target']}.jsonl" for item in mappings}
    actual_files = {item.name for item in target_dir.iterdir()}
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise ValueError(
            "target snapshot inventory mismatch "
            f"(missing={missing}, extra={extra})"
        )

    reports: list[dict[str, Any]] = []
    for mapping in mappings:
        source_name = mapping["source"]
        target_name = f"{mapping['target_schema']}__{mapping['target']}.jsonl"
        source_columns = mapping["columns"]
        target_columns = set(source_columns.values())
        source_rows = [
            {source_columns[column]: value for column, value in row.items()}
            for row in source_by_table[source_name]
        ]
        target_rows = _read_target(target_dir / target_name, target_columns)
        primary_key = [source_columns[column] for column in mapping["primary_key"]]
        source_index = _index_rows(source_rows, primary_key, source_name)
        target_index = _index_rows(target_rows, primary_key, target_name)
        source_keys, target_keys = set(source_index), set(target_index)
        common = source_keys & target_keys
        changed = sum(source_index[key] != target_index[key] for key in common)
        missing = len(source_keys - target_keys)
        extra = len(target_keys - source_keys)
        matched = missing == 0 and extra == 0 and changed == 0
        reports.append({
            "source_table": source_name,
            "target_table": f"{mapping['target_schema']}.{mapping['target']}",
            "source_rows": len(source_index),
            "target_rows": len(target_index),
            "missing_in_target": missing,
            "extra_in_target": extra,
            "changed_rows": changed,
            "source_checksum": _checksum(source_index),
            "target_checksum": _checksum(target_index),
            "matched": matched,
        })

    return {
        "mode": "read-only-parity-verification",
        "writes_performed": False,
        "tables": len(reports),
        "matched_tables": sum(item["matched"] for item in reports),
        "rows_source": sum(item["source_rows"] for item in reports),
        "rows_target": sum(item["target_rows"] for item in reports),
        "missing_in_target": sum(item["missing_in_target"] for item in reports),
        "extra_in_target": sum(item["extra_in_target"] for item in reports),
        "changed_rows": sum(item["changed_rows"] for item in reports),
        "matched": all(item["matched"] for item in reports),
        "table_reports": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--target-snapshot-dir", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = verify_parity(args.export_dir, args.target_snapshot_dir, args.mapping)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"matched": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
