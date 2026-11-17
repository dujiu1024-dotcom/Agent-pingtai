#!/usr/bin/env python3
"""Dry-run legacy SQLite → PostgreSQL snapshot reconciliation.

This command never opens a PostgreSQL connection and never writes to either
database. It compares mapped source rows with independently exported target
JSONL snapshots, emitting counts and checksums only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path, PurePosixPath
from typing import Any

_SENSITIVE = re.compile(r"(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)", re.I)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a simple SQL identifier")
    return '"' + value + '"'


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$binary_base64": base64.b64encode(value).decode("ascii")}
    return value


def _load_mapping(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("mapping must be a regular, non-symlink file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    chains = payload.get("chains") if isinstance(payload, dict) else None
    if not isinstance(chains, list) or len(chains) < 3:
        raise ValueError("mapping must define at least three business chains")
    names: set[str] = set()
    tables: list[dict[str, Any]] = []
    targets: set[str] = set()
    for chain in chains:
        if not isinstance(chain, dict):
            raise ValueError("each business chain must be an object")
        name = chain.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("business chain names must be unique non-empty strings")
        names.add(name)
        entries = chain.get("tables")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{name}: at least one table mapping is required")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"{name}: invalid table mapping")
            source_table = _safe_identifier(entry.get("source_table"), "source_table")
            target_table = _safe_identifier(entry.get("target_table"), "target_table")
            key = entry.get("key")
            key = _safe_identifier(key, "key")
            columns = entry.get("columns")
            if not isinstance(columns, dict) or not columns:
                raise ValueError(f"{name}: columns must be a non-empty object")
            normalized: dict[str, str] = {}
            for source, target in columns.items():
                source_id = _safe_identifier(source, "source column")
                target_id = _safe_identifier(target, "target column")
                if _SENSITIVE.search(source) or _SENSITIVE.search(target):
                    raise ValueError(f"{name}: sensitive columns cannot be migrated by this acceptance harness")
                normalized[source] = target
            if len(set(normalized.values())) != len(normalized):
                raise ValueError(f"{name}: target columns must be unique")
            if key[1:-1] not in normalized or normalized[key[1:-1]] != key[1:-1]:
                raise ValueError(f"{name}: primary key must map to itself")
            filename = entry.get("target_file")
            if not isinstance(filename, str):
                raise ValueError(f"{name}: target_file is required")
            relative = PurePosixPath(filename)
            if relative.is_absolute() or len(relative.parts) != 1 or relative.name in {"", ".", ".."}:
                raise ValueError(f"{name}: target_file must be a plain filename")
            if filename in targets:
                raise ValueError(f"duplicate target snapshot filename: {filename}")
            targets.add(filename)
            tables.append({
                "chain": name, "source_table": source_table[1:-1],
                "target_table": target_table[1:-1], "key": key[1:-1],
                "columns": normalized, "target_file": filename,
            })
    if len(names) < 3:
        raise ValueError("at least three distinct business chains are required")
    return tables


def _read_source(connection: sqlite3.Connection, mapping: dict[str, Any]) -> dict[str, dict[str, Any]]:
    table = _safe_identifier(mapping["source_table"], "source_table")
    schema = connection.execute(f"PRAGMA table_info({table})").fetchall()
    available = {row[1] for row in schema}
    columns = mapping["columns"]
    missing = set(columns) - available
    if missing:
        raise ValueError(f"{mapping['source_table']}: source columns missing from snapshot")
    pk_rows = [row for row in schema if row[5]]
    if len(pk_rows) != 1 or pk_rows[0][1] != mapping["key"]:
        raise ValueError(f"{mapping['source_table']}: expected a single-column primary key {mapping['key']}")
    selected = list(columns)
    projection = ",".join(_safe_identifier(name, "source column") for name in selected)
    rows: dict[str, dict[str, Any]] = {}
    query = f"SELECT {projection} FROM {table} ORDER BY {_safe_identifier(mapping['key'], 'key')}"
    for row in connection.execute(query):
        mapped = {columns[name]: _json_value(row[index]) for index, name in enumerate(selected)}
        key = str(mapped[mapping["key"]])
        if key in rows:
            raise ValueError(f"{mapping['source_table']}: duplicate primary key in source")
        rows[key] = mapped
    return rows


def _read_target(path: Path, mapping: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"target snapshot must be a regular, non-symlink file: {path.name}")
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_number}: invalid JSONL") from exc
            if not isinstance(row, dict) or set(row) != set(mapping["columns"].values()):
                raise ValueError(f"{path.name}:{line_number}: row columns do not match mapping")
            key = str(row.get(mapping["key"]))
            if key == "None" or key in rows:
                raise ValueError(f"{path.name}:{line_number}: missing or duplicate primary key")
            rows[key] = row
    return rows


def _checksum(rows: dict[str, dict[str, Any]]) -> str:
    payload = "\n".join(_canonical(rows[key]) for key in sorted(rows))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dry_run(source: Path, target_dir: Path, mapping_path: Path) -> dict[str, Any]:
    if source.is_symlink() or not source.is_file():
        raise ValueError("source must be a regular, non-symlink SQLite snapshot")
    if target_dir.is_symlink() or not target_dir.is_dir():
        raise ValueError("target snapshot directory must be a real directory")
    mappings = _load_mapping(mapping_path)
    source = source.resolve()
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    reports = []
    try:
        for mapping in mappings:
            source_rows = _read_source(connection, mapping)
            target_rows = _read_target(target_dir / mapping["target_file"], mapping)
            source_keys, target_keys = set(source_rows), set(target_rows)
            common = source_keys & target_keys
            changed = sum(source_rows[key] != target_rows[key] for key in common)
            reports.append({
                "chain": mapping["chain"],
                "source_table": mapping["source_table"],
                "target_table": mapping["target_table"],
                "source_rows": len(source_rows),
                "target_rows": len(target_rows),
                "missing_in_target": len(source_keys - target_keys),
                "extra_in_target": len(target_keys - source_keys),
                "changed_rows": changed,
                "matched_rows": len(common) - changed,
                "source_checksum": _checksum(source_rows),
                "target_checksum": _checksum(target_rows),
                "matched": source_rows == target_rows,
            })
    finally:
        connection.close()
    passed = all(item["matched"] for item in reports)
    return {
        "mode": "dry-run",
        "writes_performed": False,
        "postgres_connection_opened": False,
        "business_chains": len({item["chain"] for item in reports}),
        "tables": len(reports),
        "matched": passed,
        "rows_source": sum(item["source_rows"] for item in reports),
        "rows_target": sum(item["target_rows"] for item in reports),
        "missing_in_target": sum(item["missing_in_target"] for item in reports),
        "extra_in_target": sum(item["extra_in_target"] for item in reports),
        "changed_rows": sum(item["changed_rows"] for item in reports),
        "chain_reports": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="脱敏 SQLite 快照")
    parser.add_argument("--target-snapshot-dir", required=True, type=Path,
                        help="独立导出的 PostgreSQL 表 JSONL 快照目录")
    parser.add_argument("--mapping", required=True, type=Path, help="业务链/表/列映射 JSON")
    parser.add_argument("--dry-run", action="store_true", required=True,
                        help="只读校验；本工具不提供写入模式")
    args = parser.parse_args()
    try:
        report = dry_run(args.source, args.target_snapshot_dir, args.mapping)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"matched": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
