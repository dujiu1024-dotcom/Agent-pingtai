#!/usr/bin/env python3
"""Create a read-only inventory of legacy files, SQLite tables, identity and risks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_platform.data_governance.legacy_inventory import build_legacy_inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="legacy repository root")
    parser.add_argument("--db", type=Path, help="legacy SQLite database; defaults to agency/state/agency.db")
    parser.add_argument("--output", type=Path, help="write JSON report to a new file; stdout is always emitted")
    args = parser.parse_args()
    try:
        report = build_legacy_inventory(args.root, args.db)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            output = args.output.expanduser()
            if output.exists() or output.is_symlink():
                raise FileExistsError(f"output must be a new file: {output}")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
        return 0
    except (OSError, ValueError) as exc:
        print(f"inventory failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
