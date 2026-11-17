"""Run the offline C-21-04 quality/capacity baseline and emit JSON."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from agent_platform.observability.quality_capacity_baseline import (
    CapacityConfig,
    run_quality_capacity_baseline,
)


async def _run(args: argparse.Namespace) -> str:
    report = await run_quality_capacity_baseline(
        CapacityConfig(operations=args.operations, concurrency=args.concurrency)
    )
    return report.to_json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operations", type=int, default=256)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = asyncio.run(_run(args))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
