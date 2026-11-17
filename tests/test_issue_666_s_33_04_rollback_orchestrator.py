from __future__ import annotations

import asyncio
import unittest

from agent_platform.switching.rollback import (
    RollbackCoordinator,
    RollbackDataReport,
    RollbackExecutionError,
)


def _report(**overrides: object) -> RollbackDataReport:
    values: dict[str, object] = {
        "source_tables": ("agents", "runs", "messages"),
        "target_tables": ("agents", "runs", "messages"),
        "source_rows": 12,
        "target_rows": 12,
        "source_sha256": "a" * 64,
        "target_sha256": "a" * 64,
    }
    values.update(overrides)
    return RollbackDataReport(**values)  # type: ignore[arg-type]


class RollbackCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_runs_backfill_and_parity_before_gateway_switch(self) -> None:
        events: list[str] = []

        async def quiesce(rollback_id: str) -> None:
            events.append(f"quiesce:{rollback_id}")

        async def backfill(rollback_id: str) -> RollbackDataReport:
            events.append(f"backfill:{rollback_id}")
            return _report()

        async def verify(rollback_id: str, report: RollbackDataReport) -> bool:
            events.append(f"verify:{rollback_id}:{report.source_rows}")
            return True

        async def switch(rollback_id: str) -> None:
            events.append(f"switch:{rollback_id}")

        async def resume(rollback_id: str) -> None:
            events.append(f"resume:{rollback_id}")

        result = await RollbackCoordinator(
            quiesce_platform_writes=quiesce,
            backfill_postgres_to_sqlite=backfill,
            verify_legacy_parity=verify,
            switch_gateway_to_legacy=switch,
            resume_legacy_writes=resume,
        ).execute("rollback-001")

        self.assertEqual(
            events,
            [
                "quiesce:rollback-001",
                "backfill:rollback-001",
                "verify:rollback-001:12",
                "switch:rollback-001",
                "resume:rollback-001",
            ],
        )
        self.assertTrue(result.gateway_switched)
        self.assertEqual(result.data_report.source_sha256, "a" * 64)
        self.assertLess(result.elapsed_seconds, 300)

    async def test_mismatched_backfill_never_switches_gateway_or_resumes_writes(self) -> None:
        events: list[str] = []

        async def record(name: str) -> None:
            events.append(name)

        async def backfill(_: str) -> RollbackDataReport:
            return _report(target_rows=11)

        async def verify(_: str, __: RollbackDataReport) -> bool:
            events.append("verify")
            return True

        coordinator = RollbackCoordinator(
            quiesce_platform_writes=lambda _: record("quiesce"),
            backfill_postgres_to_sqlite=backfill,
            verify_legacy_parity=verify,
            switch_gateway_to_legacy=lambda _: record("switch"),
            resume_legacy_writes=lambda _: record("resume"),
        )
        with self.assertRaises(RollbackExecutionError) as caught:
            await coordinator.execute("rollback-002")

        self.assertEqual(caught.exception.stage, "backfill_validation")
        self.assertFalse(caught.exception.gateway_switched)
        self.assertEqual(events, ["quiesce"])

    async def test_host_verification_must_explicitly_accept_parity(self) -> None:
        events: list[str] = []

        async def backfill(_: str) -> RollbackDataReport:
            return _report()

        async def verify(_: str, __: RollbackDataReport) -> bool:
            return False

        async def mark(name: str, _: str) -> None:
            events.append(name)

        coordinator = RollbackCoordinator(
            quiesce_platform_writes=lambda _: mark("quiesce", _),
            backfill_postgres_to_sqlite=backfill,
            verify_legacy_parity=verify,
            switch_gateway_to_legacy=lambda _: mark("switch", _),
            resume_legacy_writes=lambda _: mark("resume", _),
        )
        with self.assertRaises(RollbackExecutionError) as caught:
            await coordinator.execute("rollback-003")

        self.assertEqual(caught.exception.stage, "verify_legacy_parity")
        self.assertFalse(caught.exception.gateway_switched)
        self.assertEqual(events, ["quiesce"])

    async def test_deadline_expiry_keeps_gateway_on_platform(self) -> None:
        events: list[str] = []

        async def slow_quiesce(_: str) -> None:
            await asyncio.sleep(0.05)
            events.append("quiesced")

        async def unused_backfill(_: str) -> RollbackDataReport:
            events.append("backfill")
            return _report()

        async def unused_verify(_: str, __: RollbackDataReport) -> bool:
            return True

        async def mark_switch(_: str) -> None:
            events.append("switch")

        coordinator = RollbackCoordinator(
            quiesce_platform_writes=slow_quiesce,
            backfill_postgres_to_sqlite=unused_backfill,
            verify_legacy_parity=unused_verify,
            switch_gateway_to_legacy=mark_switch,
            resume_legacy_writes=mark_switch,
            timeout_seconds=0.01,
        )
        with self.assertRaises(RollbackExecutionError) as caught:
            await coordinator.execute("rollback-004")

        self.assertEqual(caught.exception.stage, "quiesce_platform_writes")
        self.assertFalse(caught.exception.gateway_switched)
        self.assertEqual(events, [])

    async def test_incomplete_table_inventory_is_not_parity(self) -> None:
        report = _report(
            target_tables=("agents", "runs"),
            excluded_tables=("legacy_audit",),
        )
        self.assertFalse(report.parity_verified)

    async def test_report_rejects_malformed_checksum_and_duplicate_tables(self) -> None:
        with self.assertRaises(ValueError):
            _report(source_sha256="not-a-checksum")
        with self.assertRaises(ValueError):
            _report(source_tables=("agents", "agents"))


if __name__ == "__main__":
    unittest.main()
