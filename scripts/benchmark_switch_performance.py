#!/usr/bin/env python3
"""Local, opt-in performance probes for the legacy SQLite and platform backends.

No service is started and no external endpoint is contacted unless its URL/DSN is
explicitly supplied through an environment variable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4


_REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
if _REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, _REPOSITORY_ROOT)


def _summary(samples: list[float], operations: int, elapsed: float) -> dict[str, Any]:
    ordered = sorted(samples)
    def percentile(fraction: float) -> float:
        if not ordered:
            return 0.0
        return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]
    return {
        "operations": operations,
        "elapsed_seconds": round(elapsed, 6),
        "ops_per_second": round(operations / elapsed, 2) if elapsed else 0.0,
        "latency_ms": {
            "p50": round(statistics.median(ordered), 3) if ordered else 0.0,
            "p95": round(percentile(0.95), 3),
            "p99": round(percentile(0.99), 3),
            "max": round(max(ordered), 3) if ordered else 0.0,
        },
    }


def _sqlite_worker(db_path: str, worker: int, count: int, kind: str) -> list[float]:
    connection = sqlite3.connect(db_path, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    samples: list[float] = []
    try:
        for index in range(count):
            started = time.perf_counter()
            if kind == "run":
                run_id = f"{worker}:{index}"
                with connection:
                    connection.execute(
                        "INSERT INTO legacy_runs(run_id,status,payload) VALUES(?,?,?)",
                        (run_id, "queued", "{}"),
                    )
                    connection.execute(
                        "UPDATE legacy_runs SET status=? WHERE run_id=?",
                        ("succeeded", run_id),
                    )
                    connection.execute(
                        "SELECT status FROM legacy_runs WHERE run_id=?", (run_id,)
                    ).fetchone()
            else:
                entry_id = f"{worker}:{index}"
                with connection:
                    connection.execute(
                        "INSERT INTO legacy_blackboard(entry_id,scope_id,content) VALUES(?,?,?)",
                        (entry_id, "benchmark", "{}"),
                    )
                    connection.execute(
                        "SELECT content FROM legacy_blackboard WHERE entry_id=?",
                        (entry_id,),
                    ).fetchone()
            samples.append((time.perf_counter() - started) * 1000)
    finally:
        connection.close()
    return samples


def run_sqlite_baseline(*, workers: int, operations_per_worker: int) -> dict[str, Any]:
    total = workers * operations_per_worker
    with tempfile.TemporaryDirectory(prefix="s66502-") as directory:
        db_path = str(Path(directory) / "legacy-benchmark.sqlite3")
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.executescript("""
            CREATE TABLE legacy_runs(
                run_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE legacy_blackboard(
                entry_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, content TEXT NOT NULL
            );
        """)
        connection.close()
        results: dict[str, Any] = {}
        for kind in ("run", "blackboard"):
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(_sqlite_worker, db_path, worker, operations_per_worker, kind)
                    for worker in range(workers)
                ]
                samples = [sample for future in futures for sample in future.result()]
            elapsed = time.perf_counter() - started
            results[kind] = _summary(samples, total, elapsed)
        return {
            "backend": "legacy_sqlite_local_file",
            "workers": workers,
            "operations_per_worker": operations_per_worker,
            "run_lifecycle_transaction": results["run"],
            "blackboard_write_read": results["blackboard"],
            "notes": [
                "Synthetic legacy-shaped SQLite tables; not a production snapshot.",
                "SQLite uses WAL and one connection per worker.",
                "Run throughput measures insert+transition+read transactions, not LLM execution.",
            ],
        }


async def run_postgres_pool_probe(dsn: str, *, workers: int, operations: int) -> dict[str, Any]:
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        return {"status": "unavailable", "reason": f"psycopg_pool not installed: {exc}"}
    pool = AsyncConnectionPool(
        conninfo=dsn, min_size=1, max_size=max(1, min(workers, 32)),
        open=False, timeout=30,
    )
    await pool.open(wait=True)
    schema = f"s665_bench_{uuid4().hex[:12]}"
    schema_created = False
    semaphore = asyncio.Semaphore(workers)
    pool_wait_samples: list[float] = []

    async def measure_run(index: int) -> float:
        async with semaphore:
            started = time.perf_counter()
            wait_started = time.perf_counter()
            async with pool.connection() as connection:
                pool_wait_samples.append((time.perf_counter() - wait_started) * 1000)
                async with connection.transaction():
                    await connection.execute(
                        f'INSERT INTO "{schema}".runs(run_id,status,payload) VALUES (%s,%s,%s)',
                        (index, "queued", "{}"),
                    )
                    await connection.execute(
                        f'UPDATE "{schema}".runs SET status=%s WHERE run_id=%s',
                        ("succeeded", index),
                    )
                    cursor = await connection.execute(
                        f'SELECT status FROM "{schema}".runs WHERE run_id=%s', (index,),
                    )
                    row = await cursor.fetchone()
                    if row is None or row[0] != "succeeded":
                        raise RuntimeError(f"PostgreSQL Run benchmark failed to persist operation {index}")
            return (time.perf_counter() - started) * 1000

    async def measure_blackboard(index: int) -> float:
        async with semaphore:
            started = time.perf_counter()
            wait_started = time.perf_counter()
            async with pool.connection() as connection:
                pool_wait_samples.append((time.perf_counter() - wait_started) * 1000)
                async with connection.transaction():
                    await connection.execute(
                        f'INSERT INTO "{schema}".blackboard(entry_id,scope_id,content) VALUES (%s,%s,%s)',
                        (index, "benchmark", "{}"),
                    )
                    cursor = await connection.execute(
                        f'SELECT content FROM "{schema}".blackboard WHERE entry_id=%s', (index,),
                    )
                    row = await cursor.fetchone()
                    if row is None or row[0] != "{}":
                        raise RuntimeError(
                            f"PostgreSQL Blackboard benchmark failed to persist operation {index}"
                        )
            return (time.perf_counter() - started) * 1000

    try:
        async with pool.connection() as connection:
            await connection.execute(f'CREATE SCHEMA "{schema}"')
            await connection.execute(
                f'CREATE TABLE "{schema}".runs '
                "(run_id BIGINT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            await connection.execute(
                f'CREATE TABLE "{schema}".blackboard '
                "(entry_id BIGINT PRIMARY KEY, scope_id TEXT NOT NULL, content TEXT NOT NULL)"
            )
        schema_created = True

        run_started = time.perf_counter()
        run_samples = await asyncio.gather(*(measure_run(index) for index in range(operations)))
        run_elapsed = time.perf_counter() - run_started

        blackboard_started = time.perf_counter()
        blackboard_samples = await asyncio.gather(
            *(measure_blackboard(index) for index in range(operations))
        )
        blackboard_elapsed = time.perf_counter() - blackboard_started

        wait_summary = _summary(
            pool_wait_samples, len(pool_wait_samples),
            max(run_elapsed + blackboard_elapsed, 0.000001),
        )["latency_ms"]
        return {
            "status": "measured",
            "backend": "postgres_run_and_blackboard_transaction_probe",
            "pool_max_size": max(1, min(workers, 32)),
            "workers": workers,
            "metrics": {
                "run_lifecycle_transaction": _summary(run_samples, operations, run_elapsed),
                "blackboard_write_read": _summary(
                    blackboard_samples, operations, blackboard_elapsed,
                ),
                "pool_wait_latency_ms": wait_summary,
            },
            "pool_stats": pool.get_stats(),
            "notes": [
                "Isolated synthetic tables execute the same insert/update/read transaction shape as the SQLite baseline.",
                "This measures PostgreSQL storage and pool pressure, not the full platform repositories or LLM execution.",
            ],
        }
    finally:
        if schema_created:
            async with pool.connection() as connection:
                await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await pool.close()


async def run_redis_queue_probe(url: str, *, workers: int, operations: int) -> dict[str, Any]:
    try:
        from agent_platform.events.redis_streams import RedisStreamsTransport
    except ImportError as exc:
        return {"status": "unavailable", "reason": f"Redis transport dependencies unavailable: {exc}"}
    transport = RedisStreamsTransport(url, stream_prefix=f"s66502-{uuid4().hex[:10]}")
    tenant = f"bench-{uuid4().hex}"
    topic = "queue.latency"
    group = "benchmark"
    await transport.connect()
    samples: list[float] = []
    semaphore = asyncio.Semaphore(workers)

    async def publish_and_ack(index: int) -> None:
        async with semaphore:
            event_id = str(uuid4())
            sent = time.perf_counter_ns()
            await transport.publish(
                tenant_id=tenant, topic=topic, event_id=event_id,
                payload={"sent_ns": sent, "index": index},
            )
            deadline = time.perf_counter() + 10
            while time.perf_counter() < deadline:
                received = await transport.read_group(
                    tenant_id=tenant, topic=topic, group=group,
                    consumer=f"benchmark-consumer-{index % workers}",
                    count=1, block_ms=100,
                )
                if not received:
                    continue
                for message_id, fields in received:
                    payload = json.loads(fields["payload"])
                    samples.append((time.perf_counter_ns() - int(payload["sent_ns"])) / 1_000_000)
                    await transport.acknowledge(
                        tenant_id=tenant, topic=topic, group=group, message_id=message_id,
                    )
                return
            raise TimeoutError("Redis stream message was not delivered within 10 seconds")

    try:
        await transport.ensure_group(tenant_id=tenant, topic=topic, group=group)
        started = time.perf_counter()
        await asyncio.gather(*(publish_and_ack(index) for index in range(operations)))
        elapsed = time.perf_counter() - started
        ordered = sorted(samples)
        return {
            "status": "measured",
            "backend": "redis_stream_publish_to_consumer_ack",
            "workers": workers,
            "metrics": {
                "messages": len(samples),
                "ops_per_second": round(len(samples) / elapsed, 2) if elapsed else 0.0,
                "elapsed_seconds": round(elapsed, 6),
                "latency_ms": {
                    "p50": round(statistics.median(ordered), 3) if ordered else 0.0,
                    "p95": round(ordered[int((len(ordered)-1)*0.95)], 3) if ordered else 0.0,
                    "p99": round(ordered[int((len(ordered)-1)*0.99)], 3) if ordered else 0.0,
                    "max": round(max(ordered), 3) if ordered else 0.0,
                },
            },
            "notes": [
                "Concurrent publish→consumer-read→ACK probe bounded by the configured worker count.",
                "Measures queue delivery latency and probe throughput, not full Run execution throughput.",
            ],
        }
    finally:
        await transport.close()


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {
        "benchmark": "S-665-02",
        "schema_version": 1,
        "configuration": {
            "workers": args.workers,
            "operations_per_worker": args.operations,
        },
        "legacy_sqlite": run_sqlite_baseline(
            workers=args.workers, operations_per_worker=args.operations,
        ),
        "platform_postgres_pool": {"status": "not_run", "reason": "AGENT_PLATFORM_BENCH_DSN not set"},
        "platform_redis_queue": {"status": "not_run", "reason": "AGENT_PLATFORM_BENCH_REDIS_URL not set"},
        "comparison": {
            "status": "incomplete_without_platform_services",
            "reason": "SQLite baseline alone cannot establish new-platform parity or performance superiority.",
        },
    }
    dsn = os.environ.get("AGENT_PLATFORM_BENCH_DSN")
    redis_url = os.environ.get("AGENT_PLATFORM_BENCH_REDIS_URL")
    if dsn:
        result["platform_postgres_pool"] = await run_postgres_pool_probe(
            dsn, workers=args.workers, operations=args.workers * args.operations,
        )
    if redis_url:
        result["platform_redis_queue"] = await run_redis_queue_probe(
            redis_url, workers=args.workers, operations=args.workers * args.operations,
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--operations", type=int, default=100)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 128 or args.operations < 1 or args.operations > 100_000:
        parser.error("workers must be 1..128 and operations must be 1..100000")
    print(json.dumps(asyncio.run(async_main(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
