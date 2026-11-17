"""Resumable, idempotent SQLite-to-SQLite migration batches.

The source is always opened read-only. Target rows and the migration journal are
committed in the same transaction so a crash cannot advance a cursor without
committing its corresponding data.
"""
# isort: skip_file
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_STATES = {"pending", "running", "paused", "completed", "failed"}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid SQL identifier: {value!r}")
    return '"' + value + '"'


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TableMapping:
    source_table: str
    target_table: str
    columns: Mapping[str, str]
    transforms: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        _ident(self.source_table)
        _ident(self.target_table)
        if not self.columns:
            raise ValueError("at least one column mapping is required")
        for source, target in self.columns.items():
            _ident(source)
            _ident(target)
        if len(set(self.columns.values())) != len(self.columns):
            raise ValueError("target column mappings must be unique")
        allowed = {"strip", "lower", "upper", "int", "float", "bool", "json"}
        for target, operation in (self.transforms or {}).items():
            if target not in self.columns.values() or operation not in allowed:
                raise ValueError("transform must target a mapped column and use a supported operation")

    def as_dict(self) -> dict[str, Any]:
        return {"source_table": self.source_table, "target_table": self.target_table,
                "columns": dict(sorted(self.columns.items())),
                "transforms": dict(sorted((self.transforms or {}).items()))}


class MigrationError(RuntimeError):
    """Raised when a migration batch cannot safely proceed."""


def _journal(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS migration_batches (
            batch_id TEXT PRIMARY KEY,
            config_digest TEXT NOT NULL,
            source_path TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending','running','paused','completed','failed')),
            last_source_pk TEXT,
            extracted_rows INTEGER NOT NULL DEFAULT 0,
            transformed_rows INTEGER NOT NULL DEFAULT 0,
            applied_rows INTEGER NOT NULL DEFAULT 0,
            failed_rows INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS migration_batch_items (
            batch_id TEXT NOT NULL REFERENCES migration_batches(batch_id),
            source_table TEXT NOT NULL,
            source_pk TEXT NOT NULL,
            row_digest TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(batch_id, source_table, source_pk)
        );
    """)


def _open_source(path: str | Path) -> sqlite3.Connection:
    source = Path(path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise ValueError("source must be an existing regular, non-symlink SQLite file")
    connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _primary_key(connection: sqlite3.Connection, table: str) -> str:
    rows = connection.execute(f"PRAGMA table_info({_ident(table)})").fetchall()
    keys = sorted((int(row["pk"]), str(row["name"])) for row in rows if row["pk"])
    if len(keys) != 1:
        raise MigrationError(f"{table}: exactly one primary-key column is required")
    return keys[0][1]


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({_ident(table)})")}


def _row_digest(row: Mapping[str, Any]) -> str:
    return _digest(dict(row))


def _transform(row: sqlite3.Row, mapping: TableMapping) -> dict[str, Any]:
    result = {target: row[source] for source, target in mapping.columns.items()}
    for column, operation in (mapping.transforms or {}).items():
        value = result[column]
        if value is None:
            continue
        if operation == "strip": result[column] = str(value).strip()
        elif operation == "lower": result[column] = str(value).lower()
        elif operation == "upper": result[column] = str(value).upper()
        elif operation == "int": result[column] = int(value)
        elif operation == "float": result[column] = float(value)
        elif operation == "bool":
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized not in {"true", "false", "1", "0", "yes", "no"}:
                    raise ValueError(f"{column}: cannot convert value to bool")
                result[column] = int(normalized in {"true", "1", "yes"})
            else:
                result[column] = int(bool(value))
        elif operation == "json":
            parsed = json.loads(value) if isinstance(value, str) else value
            result[column] = _canonical(parsed)
    return result


def _batch_snapshot(connection: sqlite3.Connection, batch_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM migration_batches WHERE batch_id=?", (batch_id,)).fetchone()
    if row is None:
        raise MigrationError("unknown migration batch")
    result = dict(row)
    result["state"] = str(result["state"])
    result["resumable"] = result["state"] in {"pending", "paused", "failed"}
    return result


def run_migration_batch(
    source_path: str | Path,
    target_path: str | Path,
    mappings: list[TableMapping],
    *,
    batch_id: str,
    batch_size: int = 500,
    max_chunks: int | None = None,
) -> dict[str, Any]:
    """Extract, transform and upsert rows in resumable atomic chunks.

    A stable batch_id is the idempotency key. Reusing it with a different source
    or mapping is rejected. max_chunks is useful for controlled pause/resume.
    """
    if not batch_id or not isinstance(batch_id, str):
        raise ValueError("batch_id is required")
    if batch_size < 1 or (max_chunks is not None and max_chunks < 1):
        raise ValueError("batch_size and max_chunks must be positive")
    if not mappings or len({m.source_table for m in mappings}) != len(mappings):
        raise ValueError("mappings must be non-empty with unique source tables")
    source_file = Path(source_path).expanduser().resolve()
    target_file = Path(target_path).expanduser()
    if target_file.is_symlink() or source_file == target_file.resolve():
        raise ValueError("target must not be a symlink or the source database")
    source = _open_source(source_path)
    target = sqlite3.connect(str(target_file))
    target.row_factory = sqlite3.Row
    source_stat = Path(source_path).expanduser().stat()
    config = {"source": str(Path(source_path).expanduser().resolve()),
              "source_signature": {"size": source_stat.st_size, "mtime_ns": source_stat.st_mtime_ns},
              "mappings": [item.as_dict() for item in mappings]}
    config_digest = _digest(config)
    chunks = 0
    try:
        target.execute("PRAGMA foreign_keys=ON")
        _journal(target)
        target.commit()
        existing = target.execute(
            "SELECT config_digest,state FROM migration_batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if existing and existing["config_digest"] != config_digest:
            raise MigrationError("batch_id already belongs to a different source or mapping")
        if existing and existing["state"] == "completed":
            return _batch_snapshot(target, batch_id)
        if not existing:
            target.execute(
                "INSERT INTO migration_batches(batch_id,config_digest,source_path,state) "
                "VALUES(?,?,?,'pending')", (batch_id, config_digest, config["source"])
            )
            target.commit()
        for mapping in mappings:
            source_pk = _primary_key(source, mapping.source_table)
            target_pk = _primary_key(target, mapping.target_table)
            if mapping.columns.get(source_pk) != target_pk:
                raise MigrationError("source primary key must map to the target primary key")
            source_columns = _table_columns(source, mapping.source_table)
            target_columns = _table_columns(target, mapping.target_table)
            if not set(mapping.columns).issubset(source_columns):
                raise MigrationError(f"{mapping.source_table}: source mapping references missing columns")
            if not set(mapping.columns.values()).issubset(target_columns):
                raise MigrationError(f"{mapping.target_table}: target mapping references missing columns")
            cursor = target.execute(
                "SELECT last_source_pk FROM migration_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()[0]
            # Per-table cursor is encoded as a JSON object because a batch may map multiple tables.
            cursor_map = json.loads(cursor) if cursor else {}
            last_pk = cursor_map.get(mapping.source_table)
            while True:
                query = f"SELECT * FROM {_ident(mapping.source_table)}"
                params: tuple[Any, ...] = ()
                if last_pk is not None:
                    query += f" WHERE {_ident(source_pk)} > ?"
                    params = (last_pk,)
                query += f" ORDER BY {_ident(source_pk)} LIMIT ?"
                rows = source.execute(query, (*params, batch_size)).fetchall()
                if not rows:
                    break
                transformed = [_transform(row, mapping) for row in rows]
                with target:
                    target.execute(
                        "UPDATE migration_batches SET state='running',updated_at=CURRENT_TIMESTAMP,"
                        "error=NULL WHERE batch_id=?", (batch_id,)
                    )
                    for source_row, values in zip(rows, transformed):
                        pk_value = source_row[source_pk]
                        pk_text = _canonical(pk_value)
                        columns = list(values)
                        names = ",".join(_ident(name) for name in columns)
                        placeholders = ",".join("?" for _ in columns)
                        updates = ",".join(
                            f"{_ident(name)}=excluded.{_ident(name)}"
                            for name in columns if name != target_pk
                        )
                        conflict = (f"DO UPDATE SET {updates}" if updates else "DO NOTHING")
                        target.execute(
                            f"INSERT INTO {_ident(mapping.target_table)} ({names}) "
                            f"VALUES ({placeholders}) ON CONFLICT({_ident(target_pk)}) {conflict}",
                            tuple(values[name] for name in columns),
                        )
                        target.execute(
                            "INSERT INTO migration_batch_items(batch_id,source_table,source_pk,row_digest) "
                            "VALUES(?,?,?,?) ON CONFLICT(batch_id,source_table,source_pk) "
                            "DO UPDATE SET row_digest=excluded.row_digest,applied_at=CURRENT_TIMESTAMP",
                            (batch_id, mapping.source_table, pk_text, _row_digest(values)),
                        )
                    cursor_map[mapping.source_table] = rows[-1][source_pk]
                    target.execute(
                        "UPDATE migration_batches SET last_source_pk=?,extracted_rows=extracted_rows+?,"
                        "transformed_rows=transformed_rows+?,applied_rows=applied_rows+?,"
                        "updated_at=CURRENT_TIMESTAMP WHERE batch_id=?",
                        (_canonical(cursor_map), len(rows), len(transformed), len(rows), batch_id),
                    )
                chunks += 1
                if max_chunks is not None and chunks >= max_chunks:
                    with target:
                        target.execute(
                            "UPDATE migration_batches SET state='paused',updated_at=CURRENT_TIMESTAMP "
                            "WHERE batch_id=?", (batch_id,)
                        )
                    return _batch_snapshot(target, batch_id)
                last_pk = rows[-1][source_pk]
        with target:
            target.execute(
                "UPDATE migration_batches SET state='completed',updated_at=CURRENT_TIMESTAMP "
                "WHERE batch_id=?", (batch_id,)
            )
        return _batch_snapshot(target, batch_id)
    except Exception as exc:
        if target:
            with target:
                target.execute(
                    "UPDATE migration_batches SET state='failed',failed_rows=failed_rows+1,"
                    "error=?,updated_at=CURRENT_TIMESTAMP WHERE batch_id=?",
                    (type(exc).__name__, batch_id),
                )
        raise
    finally:
        source.close()
        target.close()


def migration_diff_report(
    source_path: str | Path, target_path: str | Path, mappings: list[TableMapping],
    *, batch_id: str,
) -> dict[str, Any]:
    """Compare every mapped source row and target row without exposing values.

    A migration is consistent only when the batch completed and every mapped
    target table has exactly the transformed source key set and row contents.
    Extra target rows are reported as well as missing or changed source rows.
    """
    if not mappings:
        raise ValueError("mappings must not be empty")
    source = _open_source(source_path)
    target = sqlite3.connect(str(Path(target_path).expanduser()))
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    try:
        _journal(target)
        batch = _batch_snapshot(target, batch_id)
        tables: list[dict[str, Any]] = []
        for mapping in mappings:
            pk = _primary_key(source, mapping.source_table)
            target_pk = mapping.columns[pk]
            source_rows = source.execute(
                f"SELECT * FROM {_ident(mapping.source_table)} ORDER BY {_ident(pk)}"
            ).fetchall()
            target_rows = target.execute(
                f"SELECT * FROM {_ident(mapping.target_table)} ORDER BY {_ident(target_pk)}"
            ).fetchall()
            source_keys: set[str] = set()
            target_by_key: dict[str, sqlite3.Row] = {
                _canonical(row[target_pk]): row for row in target_rows
            }
            missing = 0
            mismatched = 0
            for source_row in source_rows:
                values = _transform(source_row, mapping)
                key = _canonical(values[target_pk])
                source_keys.add(key)
                target_row = target_by_key.get(key)
                if target_row is None:
                    missing += 1
                elif _row_digest(values) != _row_digest(
                    {column: target_row[column] for column in values}
                ):
                    mismatched += 1
            extra = len(set(target_by_key) - source_keys)
            matched = len(source_rows) - missing - mismatched
            tables.append({
                "source_table": mapping.source_table,
                "target_table": mapping.target_table,
                "source_rows": len(source_rows),
                "target_rows": len(target_rows),
                "missing_target_rows": missing,
                "extra_target_rows": extra,
                "mismatched_rows": mismatched,
                "matched_rows": matched,
                "consistent": not (missing or extra or mismatched),
            })
        totals = {
            key: sum(item[key] for item in tables)
            for key in ("source_rows", "target_rows", "missing_target_rows",
                        "extra_target_rows", "mismatched_rows", "matched_rows")
        }
        consistent = batch["state"] == "completed" and all(
            item["consistent"] for item in tables
        )
        return {
            "batch_id": batch_id,
            "state": batch["state"],
            "tables": tables,
            "totals": totals,
            "consistent": consistent,
            "consistency_rate": (
                1.0 if consistent and totals["source_rows"] == totals["target_rows"]
                else (totals["matched_rows"] / totals["source_rows"]
                      if totals["source_rows"] else 0.0)
            ),
            "values_included": False,
        }
    finally:
        source.close()
        target.close()


def migration_batch_status(target_path: str | Path, batch_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(str(Path(target_path).expanduser()))
    connection.row_factory = sqlite3.Row
    try:
        _journal(connection)
        return _batch_snapshot(connection, batch_id)
    finally:
        connection.close()
