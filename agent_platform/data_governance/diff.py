"""Deterministic source/target comparison for migration dry-runs."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MigrationDiff:
    source_count: int
    target_count: int
    missing_in_target: tuple[str, ...]
    missing_in_source: tuple[str, ...]
    changed: tuple[str, ...]
    source_checksum: str
    target_checksum: str

    @property
    def matched(self) -> bool:
        return not (self.missing_in_target or self.missing_in_source or self.changed)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["matched"] = self.matched
        return value


def _index(rows: Iterable[Mapping[str, Any]], key_field: str) -> dict[str, str]:
    indexed: dict[str, str] = {}
    for row in rows:
        if key_field not in row or row[key_field] is None:
            raise ValueError(f"migration row is missing key field: {key_field}")
        key = str(row[key_field])
        if not key:
            raise ValueError("migration row key must not be empty")
        if key in indexed:
            raise ValueError(f"duplicate migration key: {key}")
        canonical = json.dumps(row, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
        indexed[key] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return indexed


def compare_migration_rows(*, source_rows: Iterable[Mapping[str, Any]],
                           target_rows: Iterable[Mapping[str, Any]],
                           key_field: str) -> MigrationDiff:
    """Compare normalized snapshots without changing either source or target."""
    if not key_field.strip():
        raise ValueError("key_field is required")
    source = _index(source_rows, key_field)
    target = _index(target_rows, key_field)
    common = source.keys() & target.keys()
    return MigrationDiff(
        source_count=len(source),
        target_count=len(target),
        missing_in_target=tuple(sorted(source.keys() - target.keys())),
        missing_in_source=tuple(sorted(target.keys() - source.keys())),
        changed=tuple(sorted(key for key in common if source[key] != target[key])),
        source_checksum=hashlib.sha256(
            json.dumps(sorted(source.items()), separators=(",", ":")).encode()
        ).hexdigest(),
        target_checksum=hashlib.sha256(
            json.dumps(sorted(target.items()), separators=(",", ":")).encode()
        ).hexdigest(),
    )
