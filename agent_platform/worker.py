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
    settings = load_production_settings()
    async def connection_factory() -> Any:
        return await _postgres_connection(settings.postgres_dsn)
    transport = RedisStreamsTransport(settings.redis_url)
    relay = OutboxRelay(
        store=PostgresOutboxStore(connection_factory=connection_factory),
        transport=transport,
        audit_sink=PostgresAuditWriter(connection_factory),
    )
    worker = OutboxRelayWorker(
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

    await worker.start()
    logger.info("production outbox worker started")
    try:
        await stop.wait()
    finally:
        await worker.stop()
        logger.info("production outbox worker stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
