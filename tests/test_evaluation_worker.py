from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.observability.evaluation_worker import (
    EvaluationSchedule,
    EvaluationScheduleWorker,
)


class FakeEvaluationService:
    def __init__(self, *, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def run(self, *, tenant_id, dataset_id, dataset_version, model_key):
        self.calls.append((tenant_id, dataset_id, dataset_version, model_key))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        if dataset_id in self.fail_ids:
            raise RuntimeError("provider credential must not be retained")


class EvaluationScheduleWorkerTests(unittest.IsolatedAsyncioTestCase):
    def schedule(self, schedule_id="nightly", dataset_id="rag-regression"):
        return EvaluationSchedule(
            schedule_id=schedule_id, tenant_id="tenant-a",
            dataset_id=dataset_id, dataset_version=7,
            model_key="model-candidate-v3", interval=timedelta(hours=1),
        )

    async def test_runs_pinned_schedule_and_respects_interval(self):
        now = datetime(2026, 11, 17, 1, tzinfo=timezone.utc)
        service = FakeEvaluationService()
        worker = EvaluationScheduleWorker(
            service=service, schedules=(self.schedule(),),
            clock=lambda: now, poll_interval_seconds=0.1,
        )
        self.assertEqual(await worker.run_once(), {"due": 1, "succeeded": 1, "failed": 0})
        self.assertEqual(service.calls, [("tenant-a", "rag-regression", 7, "model-candidate-v3")])
        self.assertEqual(await worker.run_once(), {"due": 0, "succeeded": 0, "failed": 0})
        now += timedelta(hours=1)
        self.assertEqual(await worker.run_once(), {"due": 1, "succeeded": 1, "failed": 0})

    async def test_limits_concurrent_scheduled_runs_and_retries_failure(self):
        now = datetime(2026, 11, 17, 1, tzinfo=timezone.utc)
        service = FakeEvaluationService(fail_ids={"bad-dataset"})
        worker = EvaluationScheduleWorker(
            service=service,
            schedules=(self.schedule("good"), self.schedule("bad", "bad-dataset")),
            max_concurrent_runs=1, clock=lambda: now,
        )
        self.assertEqual(await worker.run_once(), {"due": 2, "succeeded": 1, "failed": 1})
        self.assertEqual(service.max_active, 1)
        self.assertEqual(worker.last_error_types, {"bad": "RuntimeError"})
        service.fail_ids.clear()
        self.assertEqual(await worker.run_once(), {"due": 1, "succeeded": 1, "failed": 0})
        self.assertEqual(worker.last_error_types, {})

    async def test_rejects_duplicate_ids_and_invalid_schedule_configuration(self):
        item = self.schedule()
        with self.assertRaisesRegex(ValueError, "unique"):
            EvaluationScheduleWorker(service=FakeEvaluationService(), schedules=(item, item))
        for kwargs in (
            {"dataset_version": True},
            {"dataset_version": 0},
            {"interval": timedelta(milliseconds=500)},
        ):
            values = {
                "schedule_id": "bad", "tenant_id": "tenant-a",
                "dataset_id": "dataset", "dataset_version": 1,
                "model_key": "model-v1", "interval": timedelta(minutes=1),
            }
            values.update(kwargs)
            with self.assertRaises(ValueError):
                EvaluationSchedule(**values)
        with self.assertRaises(ValueError):
            EvaluationScheduleWorker(
                service=FakeEvaluationService(), schedules=(), max_concurrent_runs=True,
            )

    async def test_rejects_naive_scheduler_clock(self):
        worker = EvaluationScheduleWorker(
            service=FakeEvaluationService(), schedules=(),
            clock=lambda: datetime(2026, 11, 17),
        )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            await worker.run_once()


class FakeDistributedLeaseStore:
    def __init__(self):
        self.rows = {}
        self.lock = asyncio.Lock()

    async def claim(self, schedule, *, owner_id, now, lease_seconds):
        async with self.lock:
            key = (schedule.tenant_id, schedule.schedule_id)
            row = self.rows.get(key)
            if row is not None and (
                row["next_run_at"] > now
                or (row["lease_expires_at"] is not None and row["lease_expires_at"] > now)
            ):
                return False
            self.rows[key] = {
                "owner_id": owner_id,
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
                "next_run_at": row["next_run_at"] if row else now,
            }
            return True

    async def renew(self, schedule, *, owner_id, now, lease_seconds):
        async with self.lock:
            row = self.rows.get((schedule.tenant_id, schedule.schedule_id))
            if row is None or row["owner_id"] != owner_id or row["lease_expires_at"] <= now:
                return False
            row["lease_expires_at"] = now + timedelta(seconds=lease_seconds)
            return True

    async def complete(self, schedule, *, owner_id, now, succeeded):
        async with self.lock:
            row = self.rows.get((schedule.tenant_id, schedule.schedule_id))
            if row is None or row["owner_id"] != owner_id:
                return False
            row["next_run_at"] = now + schedule.interval if succeeded else now
            row["owner_id"] = None
            row["lease_expires_at"] = None
            return True


class DistributedEvaluationScheduleLeaseTests(unittest.IsolatedAsyncioTestCase):
    def schedule(self):
        return EvaluationSchedule(
            schedule_id="shared-nightly", tenant_id="tenant-a",
            dataset_id="rag-regression", dataset_version=7,
            model_key="model-candidate-v3", interval=timedelta(hours=1),
        )

    async def test_two_replicas_do_not_run_same_schedule_and_interval_survives_restart(self):
        now = datetime(2026, 11, 18, 1, tzinfo=timezone.utc)
        leases = FakeDistributedLeaseStore()
        service = FakeEvaluationService()
        first = EvaluationScheduleWorker(
            service=service, schedules=(self.schedule(),), clock=lambda: now,
            lease_store=leases, owner_id="replica-a",
        )
        second = EvaluationScheduleWorker(
            service=service, schedules=(self.schedule(),), clock=lambda: now,
            lease_store=leases, owner_id="replica-b",
        )

        results = await asyncio.gather(first.run_once(), second.run_once())

        self.assertEqual(sum(item["succeeded"] for item in results), 1)
        self.assertEqual(sum(item["due"] for item in results), 1)
        self.assertEqual(len(service.calls), 1)
        restarted = EvaluationScheduleWorker(
            service=service, schedules=(self.schedule(),), clock=lambda: now,
            lease_store=leases, owner_id="replica-c",
        )
        self.assertEqual(await restarted.run_once(), {"due": 0, "succeeded": 0, "failed": 0})
        self.assertEqual(len(service.calls), 1)

    async def test_failed_run_releases_lease_for_next_cycle_retry(self):
        now = datetime(2026, 11, 18, 1, tzinfo=timezone.utc)
        leases = FakeDistributedLeaseStore()
        service = FakeEvaluationService(fail_ids={"rag-regression"})
        worker = EvaluationScheduleWorker(
            service=service, schedules=(self.schedule(),), clock=lambda: now,
            lease_store=leases, owner_id="replica-a",
        )

        self.assertEqual(await worker.run_once(), {"due": 1, "succeeded": 0, "failed": 1})
        service.fail_ids.clear()
        self.assertEqual(await worker.run_once(), {"due": 1, "succeeded": 1, "failed": 0})
        self.assertEqual(len(service.calls), 2)

    async def test_worker_rejects_invalid_lease_configuration(self):
        with self.assertRaisesRegex(ValueError, "lease_seconds"):
            EvaluationScheduleWorker(
                service=FakeEvaluationService(), schedules=(self.schedule(),),
                lease_store=FakeDistributedLeaseStore(), lease_seconds=1,
            )
        with self.assertRaisesRegex(TypeError, "lease_store"):
            EvaluationScheduleWorker(
                service=FakeEvaluationService(), schedules=(self.schedule(),),
                lease_store=object(),
            )


if __name__ == "__main__":
    unittest.main()
