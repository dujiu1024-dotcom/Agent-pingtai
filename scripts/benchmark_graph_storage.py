#!/usr/bin/env python3
"""Measure PostgreSQL graph write/query latency and process memory.

Use a dedicated, pre-provisioned benchmark tenant. Rows are namespaced and
removed in a finally block; this tool never applies migrations or changes schema.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import time
import tracemalloc
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from agent_platform.graph.postgres_repository import PostgresGraphRepository
from agent_platform.graph.schema import GraphEntity, GraphRelation


@dataclass(frozen=True)
class LatencySummary:
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile + 0.5)))
    return round(ordered[index], 3)


def summarize_latencies(values: list[float]) -> LatencySummary:
    return LatencySummary(
        count=len(values),
        p50_ms=percentile(values, 0.50),
        p95_ms=percentile(values, 0.95),
        p99_ms=percentile(values, 0.99),
        max_ms=round(max(values, default=0.0), 3),
    )


def peak_rss_mb() -> float:
    # Linux reports KiB; macOS reports bytes.
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if os.uname().sysname == "Darwin" else 1024
    return round(value / divisor, 3)


async def _timed(call: Callable[[], Awaitable[Any]]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = await call()
    return result, (time.perf_counter() - started) * 1000


async def run_benchmark(
    repository: Any,
    *,
    tenant_id: str,
    prefix: str,
    workers: int,
    entities_per_worker: int,
    query_iterations: int,
) -> dict[str, Any]:
    if min(workers, entities_per_worker, query_iterations) < 1:
        raise ValueError("workers, entities_per_worker and query_iterations must be positive")
    total_entities = workers * entities_per_worker
    semaphore = asyncio.Semaphore(workers)
    entity_latencies: list[float] = []
    relation_latencies: list[float] = []
    query_latencies: list[float] = []
    adjacency_latencies: list[float] = []
    entity_ids = [f"{prefix}e{i:08d}" for i in range(total_entities)]

    async def write_entity(index: int) -> None:
        entity = GraphEntity(
            tenant_id=tenant_id,
            entity_id=entity_ids[index],
            entity_type="document",
            attributes={"title": f"{prefix} document {index}"},
        )
        async with semaphore:
            _, elapsed = await _timed(
                lambda: repository.upsert_entity(entity, name=f"{prefix} document {index}")
            )
            entity_latencies.append(elapsed)

    entity_started = time.perf_counter()
    await asyncio.gather(*(write_entity(i) for i in range(total_entities)))
    entity_elapsed = time.perf_counter() - entity_started

    async def write_relation(index: int) -> None:
        relation = GraphRelation(
            tenant_id=tenant_id,
            relation_id=f"{prefix}r{index:08d}",
            relation_type="references",
            source_entity_id=entity_ids[index],
            target_entity_id=entity_ids[(index + 1) % total_entities],
            source_entity_type="document",
            target_entity_type="document",
            attributes={"benchmark": True},
        )
        async with semaphore:
            _, elapsed = await _timed(lambda: repository.upsert_relation(relation))
            relation_latencies.append(elapsed)

    relation_started = time.perf_counter()
    await asyncio.gather(*(write_relation(i) for i in range(total_entities)))
    relation_elapsed = time.perf_counter() - relation_started

    async def query_entities() -> None:
        async with semaphore:
            _, elapsed = await _timed(
                lambda: repository.search_entities(prefix, limit=min(total_entities, 500))
            )
            query_latencies.append(elapsed)

    async def query_adjacency() -> None:
        async with semaphore:
            _, elapsed = await _timed(
                lambda: repository.list_relations(entity_ids[0], direction="both", limit=200)
            )
            adjacency_latencies.append(elapsed)

    await asyncio.gather(
        *(query_entities() for _ in range(query_iterations)),
        *(query_adjacency() for _ in range(query_iterations)),
    )
    return {
        "workload": {
            "workers": workers,
            "entities": total_entities,
            "relations": total_entities,
            "entity_query_iterations": query_iterations,
            "adjacency_query_iterations": query_iterations,
        },
        "writes": {
            "entity": {
                "latency": asdict(summarize_latencies(entity_latencies)),
                "elapsed_seconds": round(entity_elapsed, 3),
                "throughput_per_second": round(total_entities / entity_elapsed, 2)
                if entity_elapsed else 0.0,
            },
            "relation": {
                "latency": asdict(summarize_latencies(relation_latencies)),
                "elapsed_seconds": round(relation_elapsed, 3),
                "throughput_per_second": round(total_entities / relation_elapsed, 2)
                if relation_elapsed else 0.0,
            },
        },
        "queries": {
            "entity_prefix_search": asdict(summarize_latencies(query_latencies)),
            "adjacency_lookup": asdict(summarize_latencies(adjacency_latencies)),
        },
    }


async def database_metadata(dsn: str) -> dict[str, str]:
    try:
        from psycopg import AsyncConnection
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary,pool] to run graph benchmarks") from exc
    async with await AsyncConnection.connect(dsn) as connection:
        version_cursor = await connection.execute("SELECT version()")
        version = (await version_cursor.fetchone())[0]
        buffers_cursor = await connection.execute("SHOW shared_buffers")
        shared_buffers = (await buffers_cursor.fetchone())[0]
        connections_cursor = await connection.execute("SHOW max_connections")
        max_connections = (await connections_cursor.fetchone())[0]
    return {
        "postgresql_version": version,
        "shared_buffers": shared_buffers,
        "max_connections": max_connections,
    }


async def cleanup_rows(dsn: str, tenant_id: str, prefix: str) -> dict[str, int]:
    try:
        from psycopg import AsyncConnection
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary,pool] to run graph benchmarks") from exc
    async with await AsyncConnection.connect(dsn) as connection:
        async with connection.transaction():
            relations = await connection.execute(
                "DELETE FROM graph_relations WHERE tenant_id=%s AND relation_id LIKE %s",
                (tenant_id, f"{prefix}r%"),
            )
            entities = await connection.execute(
                "DELETE FROM graph_entities WHERE tenant_id=%s AND entity_id LIKE %s",
                (tenant_id, f"{prefix}e%"),
            )
    return {"relations": relations.rowcount, "entities": entities.rowcount}


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    dsn = args.dsn or os.environ.get("GRAPH_BENCHMARK_DSN")
    if not dsn:
        raise SystemExit("Set GRAPH_BENCHMARK_DSN or pass --dsn")
    if not args.confirm_isolated_tenant:
        raise SystemExit("Refusing writes without --confirm-isolated-tenant")
    if not args.tenant_id:
        raise SystemExit("--tenant-id is required and must be a dedicated benchmark tenant")

    prefix = f"bench_{uuid.uuid4().hex}_"
    repository = PostgresGraphRepository.from_dsn(tenant_id=args.tenant_id, dsn=dsn)
    tracemalloc.start()
    rss_before = peak_rss_mb()
    try:
        results = await run_benchmark(
            repository,
            tenant_id=args.tenant_id,
            prefix=prefix,
            workers=args.workers,
            entities_per_worker=args.entities_per_worker,
            query_iterations=args.query_iterations,
        )
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        results["memory"] = {
            "tracemalloc_current_mb": round(current_bytes / 1024 / 1024, 3),
            "tracemalloc_peak_mb": round(peak_bytes / 1024 / 1024, 3),
            "process_peak_rss_mb": peak_rss_mb(),
            "process_peak_rss_before_mb": rss_before,
        }
        results["metadata"] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "tenant_id": args.tenant_id,
            "namespace": prefix,
            "backend": "PostgreSQL via PostgresGraphRepository",
            "database": await database_metadata(dsn),
            "schema_migration_required": "agent_platform/storage/postgres/082_graph_storage.sql",
            "dsn": "[redacted]",
        }
    finally:
        tracemalloc.stop()
        cleanup = await cleanup_rows(dsn, args.tenant_id, prefix)
    results["cleanup"] = cleanup
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="PostgreSQL DSN; defaults to GRAPH_BENCHMARK_DSN")
    parser.add_argument("--tenant-id", required=True, help="Dedicated, pre-provisioned benchmark tenant")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--entities-per-worker", type=int, default=100)
    parser.add_argument("--query-iterations", type=int, default=200)
    parser.add_argument("--confirm-isolated-tenant", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(main_async(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
