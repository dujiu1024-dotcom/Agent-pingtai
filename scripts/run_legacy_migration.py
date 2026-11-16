#!/usr/bin/env python3
"""Run or inspect a resumable SQLite migration batch using a JSON mapping file."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent_platform.data_governance.migration_batches import (
    TableMapping,
    migration_batch_status,
    migration_diff_report,
    run_migration_batch,
)


def _load_mappings(path: Path) -> list[TableMapping]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("mapping file must contain a JSON array")
    return [TableMapping(item["source_table"], item["target_table"], item["columns"], item.get("transforms"))
            for item in payload]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-chunks", type=int)
    parser.add_argument("--action", choices=("run", "status", "diff"), default="run")
    args = parser.parse_args()
    try:
        mappings = _load_mappings(args.mapping)
        if args.action == "run":
            result = run_migration_batch(args.source, args.target, mappings,
                                         batch_id=args.batch_id, batch_size=args.batch_size,
                                         max_chunks=args.max_chunks)
        elif args.action == "status":
            result = migration_batch_status(args.target, args.batch_id)
        else:
            result = migration_diff_report(args.source, args.target, mappings,
                                           batch_id=args.batch_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"migration failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
