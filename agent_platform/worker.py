"""Production worker entrypoint for durable PostgreSQL Outbox delivery.

Run with `python -m agent_platform.worker`. PostgreSQL is the durable source
of truth; this process relays pending events to Redis Streams.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from agent_platform.events.outbox import OutboxRelay, PostgresOutboxStore
from agent_platform.events.outbox_worker import OutboxRelayWorker
from agent_platform.events.redis_streams import RedisStreamsTransport
from agent_platform.production_config import load_production_settings
from agent_platform.security.audit import PostgresAuditWriter

logger = logging.getLogger("agent_platform.worker")


async def _postgres_connection(dsn: str) -> Any:
    try:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "production worker requires psycopg[binary,pool] from requirements-platform.txt"
        ) from exc
    return await AsyncConnection.connect(dsn, row_factory=dict_row)


async def run_worker() -> None:
    # Reuse the production root and its trusted Run builder in this dedicated
    # process. The API role disables its in-process RunExecutionWorker.
    from agent_platform.production import app as production_app

    settings = production_app.state.production_settings
    run_execution = getattr(production_app.state, "run_execution", None)
    run_worker = getattr(run_execution, "task_worker", None)
    if run_worker is None:
        raise RuntimeError(
            "production Run worker requires the trusted production Run execution builder"
        )

    async def connection_factory() -> Any:
        return await _postgres_connection(settings.postgres_dsn)

    transport = RedisStreamsTransport(settings.redis_url)
    relay = OutboxRelay(
        store=PostgresOutboxStore(connection_factory=connection_factory),
        transport=transport,
        audit_sink=PostgresAuditWriter(connection_factory),
    )
    outbox_worker = OutboxRelayWorker(
        relay=relay,
        interval_seconds=1.0,
        batch_limit=100,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    await run_worker.start()
    try:
        await outbox_worker.start()
        logger.info("production Run execution and Outbox workers started")
        await stop.wait()
    finally:
        await outbox_worker.stop()
        await run_worker.stop()
        redis_client = getattr(production_app.state, "redis_client", None)
        if redis_client is not None:
            await redis_client.aclose()
        logger.info("production Run execution and Outbox workers stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
