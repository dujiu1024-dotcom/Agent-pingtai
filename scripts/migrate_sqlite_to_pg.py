#!/usr/bin/env python3
"""Import a reviewed SQLite JSONL export into pre-created PostgreSQL tables.

Dry-run is the default and uses a read-only transaction. Apply is explicit,
atomic across all mapped tables, and refuses non-empty destinations.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REDACTED = "[REDACTED]"


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a simple SQL identifier")
    return '"' + value + '"'


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$binary_base64"}:
            try:
                return base64.b64decode(value["$binary_base64"], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("invalid binary base64 value in export") from exc
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if value == _REDACTED:
        raise ValueError("export contains redacted values; refusing lossy import")
    return value


def load_export(export_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    if export_dir.is_symlink() or not export_dir.is_dir():
        raise ValueError("export directory must be a regular non-symlink directory")
    manifest_path = export_dir / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("manifest.json must be a regular non-symlink file")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != "legacy-sqlite-jsonl-v1":
        raise ValueError("unsupported export manifest format")
    if manifest.get("read_only") is not True:
        raise ValueError("export manifest must attest to read-only source access")
    tables = manifest.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("manifest must contain at least one exported table")
    names: set[str] = set()
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for item in tables:
        if not isinstance(item, dict):
            raise ValueError("invalid table entry in manifest")
        name, filename = item.get("name"), item.get("file")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("manifest table names must be non-empty and unique")
        names.add(name)
        if not isinstance(filename, str) or Path(filename).name != filename or filename in {"", ".", ".."}:
            raise ValueError(f"{name}: invalid export filename")
        file_path = export_dir / filename
        if file_path.is_symlink() or not file_path.is_file():
            raise ValueError(f"{name}: export file must be a regular non-symlink file")
        content = file_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != item.get("sha256"):
            raise ValueError(f"{name}: export checksum mismatch")
        columns = item.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError(f"{name}: manifest column inventory is required")
        column_names = [column.get("name") for column in columns if isinstance(column, dict)]
        if len(column_names) != len(columns) or len(set(column_names)) != len(column_names):
            raise ValueError(f"{name}: invalid manifest column inventory")
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(content.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("table") != name:
                raise ValueError(f"{name}:{line_number}: invalid table record")
            row = record.get("row")
            if not isinstance(row, dict) or set(row) != set(column_names):
                raise ValueError(f"{name}:{line_number}: row columns differ from manifest")
            decoded = _decode(row)
            rows.append(decoded)
        if len(rows) != item.get("rows_exported"):
            raise ValueError(f"{name}: exported row count mismatch")
        rows_by_table[name] = rows
    return manifest, rows_by_table


def load_mapping(path: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("mapping must be a regular non-symlink JSON file")
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("mapping version must be 1")
    entries = config.get("tables")
    exported = {item["name"]: item for item in manifest["tables"]}
    if not isinstance(entries, list) or len(entries) != len(exported):
        raise ValueError("mapping must cover every exported table exactly once")
    normalized: list[dict[str, Any]] = []
    seen_source: set[str] = set()
    seen_target: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("table mapping entries must be objects")
        source = entry.get("source")
        schema = entry.get("target_schema", "public")
        target = entry.get("target")
        _identifier(source, "source table")
        _identifier(schema, "target schema")
        _identifier(target, "target table")
        if source not in exported or source in seen_source:
            raise ValueError("mapping source tables must match export manifest exactly")
        if (schema, target) in seen_target:
            raise ValueError("target tables must be unique")
        columns = entry.get("columns")
        if not isinstance(columns, dict) or not columns:
            raise ValueError(f"{source}: columns mapping is required")
        source_columns = {column["name"] for column in exported[source]["columns"]}
        if set(columns) != source_columns:
            raise ValueError(f"{source}: every exported source column must be mapped exactly once")
        targets: set[str] = set()
        for src_col, dst_col in columns.items():
            _identifier(src_col, "source column")
            _identifier(dst_col, "target column")
            if dst_col in targets:
                raise ValueError(f"{source}: target column mappings must be unique")
            targets.add(dst_col)
        primary_key = entry.get("primary_key")
        if not isinstance(primary_key, list) or not primary_key:
            raise ValueError(f"{source}: primary_key must be a non-empty source-column list")
        source_pk = exported[source].get("primary_key") or []
        if set(primary_key) != set(source_pk) or any(key not in columns for key in primary_key):
            raise ValueError(f"{source}: primary_key must exactly match the exported SQLite primary key")
        normalized.append({
            "source": source, "target_schema": schema, "target": target,
            "columns": dict(columns), "primary_key": list(primary_key),
        })
        seen_source.add(source)
        seen_target.add((schema, target))
    if seen_source != set(exported):
        raise ValueError("mapping omits exported tables")
    return normalized


def _sql_table(schema: str, table: str) -> str:
    return _identifier(schema, "schema") + "." + _identifier(table, "table")


def migrate(connection: Any, rows_by_table: Mapping[str, list[dict[str, Any]]],
            mappings: list[dict[str, Any]], *, dry_run: bool) -> dict[str, Any]:
    # psycopg starts a transaction implicitly on the first statement. SET
    # TRANSACTION must be the first statement to make the whole dry-run truly
    # read-only; issuing BEGIN READ ONLY here would be a nested BEGIN and can be
    # ignored by PostgreSQL after the driver has opened its transaction.
    if dry_run:
        connection.execute("SET TRANSACTION READ ONLY")
    report: dict[str, Any] = {"mode": "dry-run" if dry_run else "apply", "tables": [], "total_rows": 0}
    try:
        for mapping in mappings:
            source = mapping["source"]
            target_sql = _sql_table(mapping["target_schema"], mapping["target"])
            if not dry_run:
                connection.execute(f"LOCK TABLE {target_sql} IN SHARE ROW EXCLUSIVE MODE")
            cursor = connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s",
                (mapping["target_schema"], mapping["target"]),
            )
            target_columns = {row[0] for row in cursor.fetchall()}
            missing_columns = set(mapping["columns"].values()) - target_columns
            if missing_columns:
                raise ValueError(f"{source}: target columns missing: {', '.join(sorted(missing_columns))}")
            cursor = connection.execute(
                "SELECT kcu.column_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_catalog=kcu.constraint_catalog "
                "AND tc.constraint_schema=kcu.constraint_schema "
                "AND tc.constraint_name=kcu.constraint_name "
                "WHERE tc.constraint_type='PRIMARY KEY' "
                "AND tc.table_schema=%s AND tc.table_name=%s "
                "ORDER BY kcu.ordinal_position",
                (mapping["target_schema"], mapping["target"]),
            )
            target_primary_key = [row[0] for row in cursor.fetchall()]
            mapped_primary_key = [mapping["columns"][column] for column in mapping["primary_key"]]
            if target_primary_key != mapped_primary_key:
                raise ValueError(f"{source}: target primary key does not match mapped source primary key")
            cursor = connection.execute(f"SELECT EXISTS (SELECT 1 FROM {target_sql} LIMIT 1)")
            if cursor.fetchone()[0]:
                raise ValueError(f"{source}: target table must be empty before migration")
            source_rows = rows_by_table[source]
            destination_columns = list(mapping["columns"].values())
            quoted_columns = ", ".join(_identifier(column, "target column") for column in destination_columns)
            placeholders = ", ".join(["%s"] * len(destination_columns))
            insert_sql = f"INSERT INTO {target_sql} ({quoted_columns}) VALUES ({placeholders})"
            seen_keys: set[tuple[Any, ...]] = set()
            for row in source_rows:
                if set(row) != set(mapping["columns"]):
                    raise ValueError(f"{source}: row columns differ from reviewed mapping")
                key = tuple(row[column] for column in mapping["primary_key"])
                if key in seen_keys:
                    raise ValueError(f"{source}: duplicate source primary key")
                seen_keys.add(key)
                values = tuple(row[source_column] for source_column in mapping["columns"])
                if not dry_run:
                    connection.execute(insert_sql, values)
            report["tables"].append({
                "source": source, "target": f'{mapping["target_schema"]}.{mapping["target"]}',
                "rows": len(source_rows), "columns": len(destination_columns),
            })
            report["total_rows"] += len(source_rows)
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
        report["status"] = "validated" if dry_run else "committed"
        return report
    except Exception:
        connection.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--dsn", default=os.environ.get("AGENT_PLATFORM_MIGRATION_DSN"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate only; this is the default")
    mode.add_argument("--apply", action="store_true", help="write all tables in one transaction")
    args = parser.parse_args()
    try:
        manifest, rows = load_export(args.export_dir)
        mappings = load_mapping(args.mapping, manifest)
        if not args.dsn:
            raise ValueError("--dsn or AGENT_PLATFORM_MIGRATION_DSN is required")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is required; install requirements-platform.txt") from exc
        with psycopg.connect(args.dsn) as connection:
            report = migrate(connection, rows, mappings, dry_run=not args.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
