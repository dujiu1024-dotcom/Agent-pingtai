#!/usr/bin/env python3
"""Measure PostgreSQL graph writes, indexed lookups, adjacency latency and relation size.

Use only a dedicated benchmark tenant. Rows are tagged and removed in finally.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def percentile(samples: list[float], quantile: float) -> float:
    if not samples:
        raise ValueError("samples must not be empty")
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    return round(sorted(samples)[max(0, math.ceil(quantile * len(samples)) - 1)], 3)


def summarize(samples: list[float]) -> dict[str, float]:
    if not samples:
        raise ValueError("samples must not be empty")
    return {
        "count": len(samples),
        "min_ms": round(min(samples), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
        "p50_ms": percentile(samples, 0.50),
        "p95_ms": percentile(samples, 0.95),
        "p99_ms": percentile(samples, 0.99),
        "max_ms": round(max(samples), 3),
    }


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Install requirements-platform.txt (psycopg)") from exc

    dsn = os.getenv("AGENT_PLATFORM_BENCH_DSN")
    tenant_id = os.getenv("AGENT_PLATFORM_BENCH_TENANT_ID")
    if not dsn or not tenant_id:
        raise RuntimeError("AGENT_PLATFORM_BENCH_DSN and AGENT_PLATFORM_BENCH_TENANT_ID are required")
    project_id = os.getenv("AGENT_PLATFORM_BENCH_PROJECT_ID")
    project_scope = [project_id] if project_id else []
    run_id = uuid.uuid4().hex
    entity_ids = [f"bench-{run_id}-{i}" for i in range(args.entities)]
    relation_ids = [f"bench-rel-{run_id}-{i}" for i in range(args.relations)]

    async def connect():
        return await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)

    async with await connect() as conn:
        exists = await (await conn.execute(
            "SELECT 1 FROM platform_tenants WHERE tenant_id=%s", (tenant_id,)
        )).fetchone()
        if exists is None:
            raise RuntimeError("benchmark tenant does not exist; refusing to create tenant")
        server = await (await conn.execute(
            "SELECT current_setting('server_version') AS server_version, "
            "current_setting('shared_buffers') AS shared_buffers, "
            "current_setting('effective_cache_size') AS effective_cache_size, "
            "current_setting('max_connections') AS max_connections"
        )).fetchone()

    async def cleanup() -> None:
        async with await connect() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM platform_graph_relations WHERE tenant_id=%s "
                    "AND attributes->>'benchmark_run'=%s", (tenant_id, run_id)
                )
                await conn.execute(
                    "DELETE FROM platform_graph_entities WHERE tenant_id=%s "
                    "AND attributes->>'benchmark_run'=%s", (tenant_id, run_id)
                )

    async def insert_entities(batch: list[str]) -> None:
        async with await connect() as conn:
            async with conn.transaction():
                await conn.executemany(
                    "INSERT INTO platform_graph_entities "
                    "(tenant_id, entity_id, entity_type, project_id, attributes) "
                    "VALUES (%s,%s,'benchmark_node',%s,%s::jsonb)",
                    [(tenant_id, eid, project_id,
                      json.dumps({"label": eid, "benchmark_run": run_id})) for eid in batch],
                )

    async def insert_relations(batch: list[tuple[str, str, str]]) -> None:
        async with await connect() as conn:
            async with conn.transaction():
                await conn.executemany(
                    "INSERT INTO platform_graph_relations "
                    "(tenant_id, relation_id, relation_type, source_entity_id, "
                    "target_entity_id, project_id, attributes) "
                    "VALUES (%s,%s,'benchmark_edge',%s,%s,%s,%s::jsonb)",
                    [(tenant_id, rid, source, target, project_id,
                      json.dumps({"benchmark_run": run_id}))
                     for rid, source, target in batch],
                )

    try:
        entity_batches = [
            entity_ids[i:i + args.batch_size]
            for i in range(0, len(entity_ids), args.batch_size)
        ]
        started = time.perf_counter()
        await asyncio.gather(*(insert_entities(batch) for batch in entity_batches))
        entity_seconds = time.perf_counter() - started

        relation_rows = [
            (relation_ids[i], entity_ids[i % len(entity_ids)],
             entity_ids[(i + 1) % len(entity_ids)])
            for i in range(args.relations)
        ]
        relation_batches = [
            relation_rows[i:i + args.batch_size]
            for i in range(0, len(relation_rows), args.batch_size)
        ]
        started = time.perf_counter()
        await asyncio.gather(*(insert_relations(batch) for batch in relation_batches))
        relation_seconds = time.perf_counter() - started

        lookup: list[float] = []
        adjacency: list[float] = []

        async def query_worker(worker: int) -> tuple[list[float], list[float]]:
            lookup_times: list[float] = []
            adjacency_times: list[float] = []
            async with await connect() as conn:
                for i in range(args.query_samples):
                    eid = entity_ids[(worker * args.query_samples + i) % len(entity_ids)]
                    started = time.perf_counter()
                    await (await conn.execute(
                        "SELECT entity_id FROM platform_graph_entities "
                        "WHERE tenant_id=%s AND entity_type='benchmark_node' "
                        "AND entity_id=%s AND (project_id IS NULL OR project_id=ANY(%s))",
                        (tenant_id, eid, project_scope),
                    )).fetchone()
                    lookup_times.append((time.perf_counter() - started) * 1000)
                    started = time.perf_counter()
                    await (await conn.execute(
                        "SELECT relation_id FROM platform_graph_relations "
                        "WHERE tenant_id=%s AND source_entity_id=%s "
                        "AND (project_id IS NULL OR project_id=ANY(%s)) "
                        "ORDER BY relation_id LIMIT 100",
                        (tenant_id, eid, project_scope),
                    )).fetchall()
                    adjacency_times.append((time.perf_counter() - started) * 1000)
            return lookup_times, adjacency_times

        results = await asyncio.gather(*(
            query_worker(worker) for worker in range(args.query_concurrency)
        ))
        for lookup_part, adjacency_part in results:
            lookup.extend(lookup_part)
            adjacency.extend(adjacency_part)

        async with await connect() as conn:
            sizes = await (await conn.execute(
                "SELECT pg_total_relation_size('platform_graph_entities') AS entities_bytes, "
                "pg_total_relation_size('platform_graph_relations') AS relations_bytes"
            )).fetchone()

        return {
            "schema": "A-22-05/graph-postgres-benchmark-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "tenant_id": tenant_id,
            "project_scoped": bool(project_id),
            "dataset": {
                "entities": args.entities, "relations": args.relations,
                "query_samples_per_worker": args.query_samples,
                "query_concurrency": args.query_concurrency,
                "write_concurrency": len(entity_batches),
            },
            "server": dict(server),
            "writes": {
                "entities_seconds": round(entity_seconds, 3),
                "entities_per_second": round(args.entities / entity_seconds, 2),
                "relations_seconds": round(relation_seconds, 3),
                "relations_per_second": round(args.relations / relation_seconds, 2),
            },
            "queries": {
                "entity_index_lookup": summarize(lookup),
                "outgoing_adjacency": summarize(adjacency),
            },
            "storage": dict(sizes),
            "cleanup": "completed",
        }
    finally:
        await cleanup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entities", type=int, default=5000)
    parser.add_argument("--relations", type=int, default=10000)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--query-samples", type=int, default=200)
    parser.add_argument("--query-concurrency", type=int, default=8)
    parser.add_argument("--output", type=Path, default=Path("graph-benchmark.json"))
    args = parser.parse_args()
    for name in ("entities", "relations", "batch_size", "query_samples", "query_concurrency"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


async def main() -> None:
    args = parse_args()
    report = await run_benchmark(args)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
