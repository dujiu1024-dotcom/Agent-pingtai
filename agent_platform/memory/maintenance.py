"""Periodic tenant-scoped cleanup for expired governed memories."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from agent_platform.memory.service import MemoryService

TenantLister = Callable[[], Awaitable[Sequence[str]]]
logger = logging.getLogger(__name__)


class MemoryExpiryWorker:
    """Runs expiry transitions and derived-index cleanup on a shared worker loop.

    The tenant list is supplied by the host control plane; the worker never
    guesses tenant IDs or scans across tenants without an explicit provider.
    """

    def __init__(
        self, *, memory: MemoryService, list_tenant_ids: TenantLister,
        interval_seconds: float = 300,
    ) -> None:
        if interval_seconds < 5:
            raise ValueError("interval_seconds must be at least 5")
        self.memory = memory
        self.list_tenant_ids = list_tenant_ids
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def run_once(self) -> dict[str, int]:
        tenant_ids = await self.list_tenant_ids()
        if len(tenant_ids) > 10000:
            raise ValueError("tenant provider returned too many tenants")
        results: dict[str, int] = {}
        for tenant_id in tenant_ids:
            if not isinstance(tenant_id, str) or not tenant_id.strip():
                raise ValueError("tenant provider returned an invalid tenant ID")
            results[tenant_id] = await self.memory.expire_due(tenant_id=tenant_id)
        return results

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("memory expiry sweep failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.interval_seconds,
                )
            except TimeoutError:
                continue

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="memory-expiry-worker")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            await task
