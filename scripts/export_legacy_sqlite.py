#!/usr/bin/env python3
"""Read-only, deterministic SQLite inventory and JSONL export for staged migrations."""
from __future__ import annotations
import argparse, base64, hashlib, json, re, sqlite3, sys
from pathlib import Path
from typing import Any

_SENSITIVE = re.compile(r"(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)", re.I)

def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'

def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$binary_base64": base64.b64encode(value).decode("ascii")}
    return value

def _schema_inventory(connection: sqlite3.Connection, selected: list[str]) -> dict[str, Any]:
    tables = []
    for table in selected:
        columns = list(connection.execute(f"PRAGMA table_info({_identifier(table)})"))
        indexes = []
        for index in connection.execute(f"PRAGMA index_list({_identifier(table)})"):
            index_name = index["name"]
            index_columns = [
                {"sequence": item["seqno"], "column": item["name"], "descending": bool(item["desc"]),
                 "collation": item["coll"], "key": bool(item["key"])}
                for item in connection.execute(f"PRAGMA index_xinfo({_identifier(index_name)})")
                if item["key"]
            ]
            indexes.append({"name": index_name, "unique": bool(index["unique"]),
                            "origin": index["origin"], "partial": bool(index["partial"]),
                            "columns": index_columns})
        foreign_keys = [
            {"id": row["id"], "sequence": row["seq"], "referenced_table": row["table"],
             "from": row["from"], "to": row["to"], "on_update": row["on_update"],
             "on_delete": row["on_delete"], "match": row["match"]}
            for row in connection.execute(f"PRAGMA foreign_key_list({_identifier(table)})")
        ]
        create_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        tables.append({
            "name": table,
            "columns": [{"name": c["name"], "type": c["type"], "notnull": bool(c["notnull"]),
                         "primary_key_order": c["pk"]} for c in columns],
            "primary_key": [c["name"] for c in sorted(columns, key=lambda c: c["pk"]) if c["pk"]],
            "foreign_keys": foreign_keys, "indexes": sorted(indexes, key=lambda item: item["name"]),
            "create_sql": create_row["sql"] if create_row else None,
        })
    objects = {}
    for object_type in ("view", "trigger"):
        objects[object_type + "s"] = [
            {"name": row["name"], "table": row["tbl_name"], "sql": row["sql"]}
            for row in connection.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type=? ORDER BY name", (object_type,)
            )
        ]
    return {"sqlite_version": sqlite3.sqlite_version,
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "tables": tables, **objects}

def export_database(database: Path, output_dir: Path, tables: list[str] | None = None) -> dict[str, Any]:
    if database.is_symlink() or not database.is_file():
        raise ValueError("database must be a regular, non-symlink file")
    if output_dir.is_symlink():
        raise ValueError("output directory must not be a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be empty: {output_dir}")
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    manifest: dict[str, Any] = {
        "format": "legacy-sqlite-jsonl-v1", "source": database.name, "read_only": True,
        "sensitive_columns": "redacted by column-name heuristic; review nested JSON/text separately",
        "tables": [],
    }
    try:
        connection.execute("BEGIN")
        available = [row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        selected = sorted(set(tables)) if tables else available
        unknown = set(selected) - set(available)
        if unknown:
            raise ValueError("unknown SQLite tables: " + ", ".join(sorted(unknown)))
        manifest["schema"] = _schema_inventory(connection, selected)
        schema_by_name = {item["name"]: item for item in manifest["schema"]["tables"]}
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
            count, digest = 0, hashlib.sha256()
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                for source_row in connection.execute(query):
                    row = {name: ("[REDACTED]" if name in sensitive else _json_value(source_row[name]))
                           for name in names}
                    record = {"table": table,
                              "primary_key": {name: row[name] for name in primary} if primary else None,
                              "row": row}
                    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"), default=str) + "\n"
                    stream.write(encoded); digest.update(encoded.encode("utf-8")); count += 1
            temporary.replace(destination)
            manifest["tables"].append({
                "name": table, "columns": schema_by_name[table]["columns"], "primary_key": primary,
                "rows_exported": count, "redacted_columns": sorted(sensitive),
                "file": destination.name, "sha256": digest.hexdigest(),
            })
        connection.rollback()
    finally:
        connection.close()
    manifest_path, temporary_manifest = output_dir / "manifest.json", output_dir / "manifest.json.tmp"
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                   encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return manifest

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="legacy SQLite database (opened read-only)")
    parser.add_argument("output_dir", type=Path, help="new or empty directory for JSONL export")
    parser.add_argument("--table", action="append", dest="tables", help="export only this table; may be repeated")
    args = parser.parse_args()
    try:
        manifest = export_database(args.database, args.output_dir, args.tables)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"export failed: {exc}", file=sys.stderr); return 2
    print(json.dumps({"tables": len(manifest["tables"]),
                      "rows": sum(item["rows_exported"] for item in manifest["tables"]),
                      "manifest": str(args.output_dir / "manifest.json")}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
