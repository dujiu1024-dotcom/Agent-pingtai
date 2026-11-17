from __future__ import annotations

import asyncio
import unittest
from typing import Any

from agent_platform.data_governance.cutover import (
    ConsistencyEvidence,
    CutoverPhase,
    CutoverPolicy,
    evaluate_cutover,
    plan_write,
)
from agent_platform.switching.gateway import CutoverGateway, TenantCutoverPolicy


class RollbackDrillTests(unittest.TestCase):
    def test_consistency_failure_forces_legacy_read_and_write_plan(self):
        evidence = ConsistencyEvidence(
            sampled_rows=1000,
            mismatched_rows=5,
            source_checksum="legacy-checksum",
            target_checksum="platform-checksum",
            replication_lag_seconds=1,
            write_error_rate=0,
            observed_seconds=900,
        )
        decision = evaluate_cutover(
            current=CutoverPhase.CANARY,
            requested=CutoverPhase.TARGET_PRIMARY,
            evidence=evidence,
            policy=CutoverPolicy(),
        )
        self.assertEqual(decision.phase, CutoverPhase.ROLLED_BACK)
        self.assertTrue(decision.rollback_required)
        self.assertIn("row_mismatch_threshold_exceeded", decision.reasons)
        write_plan = plan_write(phase=decision.phase)
        self.assertEqual(write_plan.authoritative_system, "legacy")
        self.assertEqual(write_plan.write_systems, ("legacy",))
        self.assertEqual(write_plan.replication_targets, ())

    def test_gateway_rollback_switches_same_instance_to_legacy(self):
        calls: list[str] = []

        async def new_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            calls.append("platform")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"platform"})

        async def legacy_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            calls.append("legacy")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"legacy"})

        async def resolve_tenant(scope: dict[str, Any]) -> str:
            return "tenant-canary"

        gateway = CutoverGateway(
            new_app=new_app,
            legacy_app=legacy_app,
            policy=TenantCutoverPolicy(
                enabled=True,
                tenant_allowlist=frozenset({"tenant-canary"}),
                traffic_percentage=0,
                salt=b"stable-cutover-salt",
            ),
            tenant_resolver=resolve_tenant,
        )

        async def invoke() -> list[dict[str, Any]]:
            messages: list[dict[str, Any]] = []

            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message: dict[str, Any]) -> None:
                messages.append(message)

            await gateway({"type": "http", "method": "GET", "path": "/"}, receive, send)
            return messages

        async def drill() -> None:
            await invoke()
            self.assertEqual(calls[-1], "platform")
            gateway.rollback_to_legacy()
            messages = await invoke()
            self.assertEqual(calls[-1], "legacy")
            self.assertEqual(messages[0]["status"], 200)
            self.assertEqual(messages[1]["body"], b"legacy")
            self.assertFalse(gateway.policy.enabled)
            self.assertEqual(gateway.policy.traffic_percentage, 0)

        asyncio.run(drill())


if __name__ == "__main__":
    unittest.main()
