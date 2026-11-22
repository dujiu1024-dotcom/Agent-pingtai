"""Production worker entrypoint for durable Run execution and Outbox delivery.

Run with `python -m agent_platform.worker`. The process loads the same
repository-owned production composition root as the API, then runs the
PostgreSQL-backed RunExecutionWorker and Outbox-to-Redis relay together.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from agent_platform.events.outbox import OutboxRelay, PostgresOutboxStore
from agent_platform.events.outbox_worker import OutboxRelayWorker
from agent_platform.events.redis_streams import RedisStreamsTransport
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


async def run_worker(
    *,
    production_app: Any | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the production Run and Outbox workers until a termination signal.

    Optional arguments are deterministic test seams. Normal module execution
    imports the repository-owned ASGI composition root and installs SIGINT /
    SIGTERM handlers.
    """
    if production_app is None:
        # Importing the production root validates shared configuration and
        # resolves the single trusted Run-execution builder before this worker
        # can report itself as started.
        from agent_platform.production import app as production_app

    settings = production_app.state.production_settings
    run_execution = getattr(production_app.state, "run_execution", None)
    task_worker = getattr(run_execution, "task_worker", None)
    if task_worker is None:
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

    stop = stop_event or asyncio.Event()
    if stop_event is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    run_started = False
    outbox_started = False
    try:
        await task_worker.start()
        run_started = True
        await outbox_worker.start()
        outbox_started = True
        logger.info("production Run execution and Outbox workers started")
        await stop.wait()
    finally:
        try:
            if outbox_started:
                await outbox_worker.stop()
        finally:
            try:
                if run_started:
                    await task_worker.stop()
            finally:
                # The FastAPI lifespan is not entered by this CLI process;
                # close the Redis client created by the shared production root.
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
