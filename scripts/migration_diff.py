#!/usr/bin/env python3
"""Read-only JSONL migration diff; exits 1 when source and target differ."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform.data_governance.diff import compare_migration_rows  # noqa: E402


def validate_input_paths(source: Path, target: Path) -> None:
    for label, path in (("source", source), ("target", target)):
        if path.is_symlink():
            raise ValueError(f"{label} input must not be a symlink: {path}")
        if not path.exists() or not path.is_file():
            raise ValueError(f"{label} input must be an existing regular file: {path}")
    if source.resolve() == target.resolve():
        raise ValueError("source and target inputs must be different files")
    if source.samefile(target):
        raise ValueError("source and target inputs must not refer to the same file")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: each JSONL line must be an object")
            rows.append(value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--key-field", default="id")
    args = parser.parse_args()
    try:
        validate_input_paths(args.source, args.target)
        report = compare_migration_rows(
            source_rows=load_jsonl(args.source),
            target_rows=load_jsonl(args.target),
            key_field=args.key_field,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
