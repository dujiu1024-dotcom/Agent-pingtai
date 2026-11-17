"""Production entrypoint for the durable PostgreSQL alert outbox dispatcher.

The webhook endpoint and host allowlist are deployment-owned configuration.
No notification is sent until a durable outbox row has been claimed and its
write-ahead audit record has succeeded.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from agent_platform.observability.alert_notifications import (
    AlertNotificationDispatcher as DurableAlertNotificationDispatcher,
    WebhookAlertNotificationProvider,
)
from agent_platform.observability.alert_store import PostgresAlertEventStore
from agent_platform.observability.alert_worker import AlertDispatchWorker
from agent_platform.production_config import load_production_settings
from agent_platform.security.audit import PostgresAuditWriter

logger = logging.getLogger("agent_platform.alert_dispatcher")


@dataclass(frozen=True)
class AlertDispatchSettings:
    endpoint: str
    allowed_hosts: tuple[str, ...]
    authorization_header: str | None = None
    poll_interval_seconds: float = 5.0
    batch_size: int = 100


def load_alert_dispatch_settings(
    environ: Mapping[str, str] | None = None,
) -> AlertDispatchSettings:
    env = os.environ if environ is None else environ
    endpoint = env.get("ALERT_WEBHOOK_URL", "").strip()
    hosts = tuple(
        item.strip().lower()
        for item in env.get("ALERT_WEBHOOK_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    )
    if not endpoint:
        raise ValueError("ALERT_WEBHOOK_URL is required")
    if not hosts or len(hosts) != len(set(hosts)):
        raise ValueError("ALERT_WEBHOOK_ALLOWED_HOSTS must contain unique hostnames")

    authorization_header = None
    secret_path = env.get("ALERT_WEBHOOK_AUTHORIZATION_FILE", "").strip()
    if secret_path:
        try:
            authorization_header = Path(secret_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("cannot read alert webhook authorization secret") from exc
        if not authorization_header:
            raise ValueError("alert webhook authorization secret is empty")

    try:
        poll_interval = float(env.get("ALERT_DISPATCH_POLL_INTERVAL_SECONDS", "5"))
        batch_size = int(env.get("ALERT_DISPATCH_BATCH_SIZE", "100"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid alert dispatcher polling configuration") from exc
    if not 0.1 <= poll_interval <= 3600:
        raise ValueError("alert dispatcher poll interval must be between 0.1 and 3600")
    if not 1 <= batch_size <= 500:
        raise ValueError("alert dispatcher batch size must be between 1 and 500")
    return AlertDispatchSettings(
        endpoint=endpoint,
        allowed_hosts=hosts,
        authorization_header=authorization_header,
        poll_interval_seconds=poll_interval,
        batch_size=batch_size,
    )


def build_alert_dispatch_worker(
    *,
    connection_factory: Any,
    webhook_client: Any,
    settings: AlertDispatchSettings,
) -> AlertDispatchWorker:
    """Compose only durable adapters; callers own the HTTP client lifecycle."""
    store = PostgresAlertEventStore(connection_factory)
    audit_sink = PostgresAuditWriter(connection_factory)
    provider = WebhookAlertNotificationProvider(
        client=webhook_client,
        endpoint=settings.endpoint,
        allowed_hosts=settings.allowed_hosts,
        authorization_header=settings.authorization_header,
    )
    dispatcher = DurableAlertNotificationDispatcher(
        store=store,
        provider=provider,
        audit_sink=audit_sink,
    )
    return AlertDispatchWorker(
        dispatcher,
        poll_interval_seconds=settings.poll_interval_seconds,
        batch_size=settings.batch_size,
    )


async def _postgres_connection(dsn: str) -> Any:
    try:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "alert dispatcher requires psycopg[binary,pool] from requirements-platform.txt"
        ) from exc
    return await AsyncConnection.connect(dsn, row_factory=dict_row)


async def run_alert_dispatcher() -> None:
    settings = load_production_settings()
    dispatch_settings = load_alert_dispatch_settings()
    async with httpx.AsyncClient(follow_redirects=False) as client:
        async def connection_factory() -> Any:
            return await _postgres_connection(settings.postgres_dsn)

        worker = build_alert_dispatch_worker(
            connection_factory=connection_factory,
            webhook_client=client,
            settings=dispatch_settings,
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        await worker.start()
        logger.info("production alert dispatcher started")
        try:
            await stop.wait()
        finally:
            await worker.stop()
            logger.info("production alert dispatcher stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_alert_dispatcher())


if __name__ == "__main__":
    main()
