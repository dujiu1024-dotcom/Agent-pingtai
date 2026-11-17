"""Lifecycle-managed scheduler for pinned Agent/RAG evaluation runs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4


@dataclass(frozen=True)
class EvaluationSchedule:
    """One immutable dataset/model evaluation plan."""

    schedule_id: str
    tenant_id: str
    dataset_id: str
    dataset_version: int
    model_key: str
    interval: timedelta

    def __post_init__(self) -> None:
        for name in ("schedule_id", "tenant_id", "dataset_id", "model_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if isinstance(self.dataset_version, bool) or not isinstance(self.dataset_version, int) or self.dataset_version < 1:
            raise ValueError("dataset_version must be a positive integer")
        if not isinstance(self.interval, timedelta) or self.interval < timedelta(seconds=1):
            raise ValueError("evaluation interval must be at least one second")


class EvaluationScheduleWorker:
    """Run fixed, version-pinned evaluations with optional distributed leases.

    Schedule definitions are supplied by the trusted host, not accepted from API
    callers. Without a lease store this worker is process-local. Multi-replica
    deployments should inject PostgresEvaluationScheduleLeaseStore.
    """

    def __init__(
        self, *, service: Any, schedules: tuple[EvaluationSchedule, ...],
        poll_interval_seconds: float = 5.0, max_concurrent_runs: int = 1,
        clock: Callable[[], datetime] | None = None, lease_store: Any | None = None,
        lease_seconds: float = 60.0, owner_id: str | None = None,
    ) -> None:
        if len({item.schedule_id for item in schedules}) != len(schedules):
            raise ValueError("evaluation schedule IDs must be unique")
        if isinstance(poll_interval_seconds, bool) or not isinstance(poll_interval_seconds, (int, float)) or not 0.1 <= poll_interval_seconds <= 3600:
            raise ValueError("poll interval must be between 0.1 and 3600 seconds")
        if isinstance(max_concurrent_runs, bool) or not isinstance(max_concurrent_runs, int) or not 1 <= max_concurrent_runs <= 16:
            raise ValueError("max_concurrent_runs must be an integer between 1 and 16")
        if not callable(getattr(service, "run", None)):
            raise TypeError("evaluation service must expose run()")
        if lease_store is not None and any(
            not callable(getattr(lease_store, name, None))
            for name in ("claim", "renew", "complete")
        ):
            raise TypeError("lease_store must expose claim(), renew(), and complete()")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) or not 3 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 3 and 3600")
        if owner_id is not None and (not isinstance(owner_id, str) or not owner_id.strip()):
            raise ValueError("owner_id must be a non-empty string")
        self.service = service
        self.schedules = tuple(schedules)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.max_concurrent_runs = max_concurrent_runs
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_store = lease_store
        self.lease_seconds = float(lease_seconds)
        self.owner_id = owner_id or str(uuid4())
        self._last_success: dict[str, datetime] = {}
        self._in_flight: set[str] = set()
        self._run_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.cycles = 0
        self.last_result: dict[str, int] = {"due": 0, "succeeded": 0, "failed": 0}
        self.last_error_types: dict[str, str] = {}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _renew_while_running(
        self, item: EvaluationSchedule, run_task: asyncio.Task[Any],
        lease_lost: asyncio.Event,
    ) -> None:
        while not run_task.done():
            await asyncio.sleep(self.lease_seconds / 3)
            if run_task.done():
                return
            try:
                renewed = await self.lease_store.renew(
                    item, owner_id=self.owner_id, now=self.clock(),
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                renewed = False
            if not renewed:
                lease_lost.set()
                run_task.cancel()
                return

    async def run_once(self) -> dict[str, int]:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("evaluation scheduler clock must return timezone-aware datetime")
        async with self._run_lock:
            due = tuple(
                item for item in self.schedules
                if item.schedule_id not in self._in_flight
                and (
                    item.schedule_id not in self._last_success
                    or now - self._last_success[item.schedule_id] >= item.interval
                )
            )
            self._in_flight.update(item.schedule_id for item in due)

        semaphore = asyncio.Semaphore(self.max_concurrent_runs)

        async def execute(item: EvaluationSchedule) -> bool | None:
            claimed = False
            succeeded = False
            run_task: asyncio.Task[Any] | None = None
            heartbeat_task: asyncio.Task[None] | None = None
            lease_lost = asyncio.Event()
            try:
                async with semaphore:
                    if self.lease_store is not None:
                        claimed = await self.lease_store.claim(
                            item, owner_id=self.owner_id, now=self.clock(),
                            lease_seconds=self.lease_seconds,
                        )
                        if not claimed:
                            return None
                    run_task = asyncio.create_task(self.service.run(
                        tenant_id=item.tenant_id, dataset_id=item.dataset_id,
                        dataset_version=item.dataset_version, model_key=item.model_key,
                    ))
                    if claimed:
                        heartbeat_task = asyncio.create_task(
                            self._renew_while_running(item, run_task, lease_lost)
                        )
                    await run_task
                    if lease_lost.is_set():
                        raise RuntimeError("evaluation schedule lease was lost")
                    succeeded = True
                    self._last_success[item.schedule_id] = self.clock()
                    self.last_error_types.pop(item.schedule_id, None)
                    return True
            except asyncio.CancelledError:
                if lease_lost.is_set():
                    self.last_error_types[item.schedule_id] = "LeaseLostError"
                    return False
                raise
            except Exception as exc:
                self.last_error_types[item.schedule_id] = type(exc).__name__
                return False
            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
                if claimed:
                    await self.lease_store.complete(
                        item, owner_id=self.owner_id, now=self.clock(),
                        succeeded=succeeded and not lease_lost.is_set(),
                    )
                self._in_flight.discard(item.schedule_id)

        outcomes = await asyncio.gather(*(execute(item) for item in due))
        result = {
            "due": sum(outcome is not None for outcome in outcomes),
            "succeeded": sum(outcome is True for outcome in outcomes),
            "failed": sum(outcome is False for outcome in outcomes),
        }
        self.last_result = result
        self.cycles += 1
        return dict(result)

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or self._stop
        while not stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error_types["scheduler"] = type(exc).__name__
                self.cycles += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                continue

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self.run_forever(), name="platform-evaluation-scheduler",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            self._stop.set()
            return
        self._stop.set()
        try:
            await task
        finally:
            self._task = None
