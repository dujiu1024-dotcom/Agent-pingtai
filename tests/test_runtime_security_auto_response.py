from __future__ import annotations

import unittest

from agent_platform.observability.security_response import (
    DetectionRule, ResponseAction, ResponseRule, RuntimeSecurityAutoResponder,
    RuntimeSecurityObservation, RuleBasedSecurityPolicy, SecurityResponseExecutor,
    SecuritySeverity, ThresholdSecurityDetector,
)


class FakeAudit:
    def __init__(self, fail_on=None):
        self.events = []
        self.fail_on = fail_on

    async def record(self, **kwargs):
        if kwargs["action"] == self.fail_on:
            raise RuntimeError("audit backend unavailable")
        self.events.append(kwargs)


class FakeStore:
    def __init__(self):
        self.claimed = set()
        self.completed = []

    async def claim_once(self, *, tenant_id, event_id):
        key = (tenant_id, event_id)
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    async def complete(self, *, tenant_id, event_id, outcome):
        self.completed.append((tenant_id, event_id, outcome))


class SecurityAutoResponseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.observation = RuntimeSecurityObservation(
            observation_id="evt-001", tenant_id="tenant-a", run_id="run-a",
            actor_id="agent-a", event_type="credential_anomaly", signal="failed_auth",
            observed_value=9, occurred_at="2026-11-17T03:00:00Z",
            resource_id="credential-7", attributes={"token": "secret-token", "source": "gateway"},
        )
        self.actions = []
        async def interrupt(event, reason):
            self.actions.append((ResponseAction.INTERRUPT_RUN, event.run_id, reason))
        async def isolate_tenant(event, reason):
            self.actions.append((ResponseAction.ISOLATE_TENANT, event.tenant_id, reason))
        self.executor = SecurityResponseExecutor({
            ResponseAction.INTERRUPT_RUN: interrupt,
            ResponseAction.ISOLATE_TENANT: isolate_tenant,
        })
        self.detector = ThresholdSecurityDetector((
            DetectionRule("credential_anomaly", "failed_auth", 5, severity=SecuritySeverity.HIGH),
        ))
        self.policy = RuleBasedSecurityPolicy((
            ResponseRule("credential_anomaly", SecuritySeverity.HIGH,
                         ResponseAction.INTERRUPT_RUN, "credential anomaly threshold exceeded"),
        ), version="sec-policy-v7")
        self.audit = FakeAudit()
        self.store = FakeStore()
        self.service = RuntimeSecurityAutoResponder(
            detector=self.detector, policy=self.policy, executor=self.executor,
            audit=self.audit, event_store=self.store,
        )

    async def test_detect_decide_act_and_audit_in_order(self):
        result = await self.service.handle(self.observation)
        self.assertEqual(result.status, "action_succeeded")
        self.assertEqual(result.action, "interrupt_run")
        self.assertEqual(self.actions[0][1], "run-a")
        self.assertEqual(
            [e["action"] for e in self.audit.events],
            ["security.response.decision", "security.response.action",
             "security.response.action"],
        )
        self.assertEqual(self.audit.events[0]["outcome"], "succeeded")
        self.assertEqual(self.audit.events[-1]["outcome"], "succeeded")
        self.assertNotIn("secret-token", str(self.audit.events))
        self.assertEqual(self.store.completed[-1][-1], "action_succeeded")

    async def test_below_threshold_is_not_detected(self):
        observation = RuntimeSecurityObservation(
            observation_id="evt-low", tenant_id="tenant-a", run_id="run-a",
            actor_id="agent-a", event_type="credential_anomaly", signal="failed_auth",
            observed_value=2, occurred_at="2026-11-17T03:00:00Z",
        )
        result = await self.service.handle(observation)
        self.assertEqual(result.status, "no_detection")
        self.assertEqual(self.actions, [])
        self.assertEqual(self.audit.events, [])

    async def test_unknown_policy_event_is_audited_without_action(self):
        policy = RuleBasedSecurityPolicy((), version="v1")
        service = RuntimeSecurityAutoResponder(
            detector=self.detector, policy=policy, executor=self.executor,
            audit=self.audit, event_store=self.store,
        )
        result = await service.handle(self.observation)
        self.assertEqual(result.status, "no_action")
        self.assertEqual(self.actions, [])
        self.assertEqual(self.audit.events[0]["outcome"], "denied")

    async def test_duplicate_event_does_not_repeat_action(self):
        first = await self.service.handle(self.observation)
        second = await self.service.handle(self.observation)
        self.assertEqual(first.status, "action_succeeded")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(len(self.actions), 1)

    async def test_write_ahead_audit_failure_blocks_action(self):
        audit = FakeAudit(fail_on="security.response.action")
        service = RuntimeSecurityAutoResponder(
            detector=self.detector, policy=self.policy, executor=self.executor,
            audit=audit, event_store=self.store,
        )
        result = await service.handle(self.observation)
        self.assertEqual(result.status, "audit_blocked")
        self.assertEqual(self.actions, [])
        self.assertEqual(self.store.completed[-1][-1], "audit_blocked")

    async def test_action_failure_is_audited(self):
        async def broken(event, reason):
            raise RuntimeError("isolation service unavailable")
        executor = SecurityResponseExecutor({ResponseAction.INTERRUPT_RUN: broken})
        service = RuntimeSecurityAutoResponder(
            detector=self.detector, policy=self.policy, executor=executor,
            audit=self.audit, event_store=self.store,
        )
        result = await service.handle(self.observation)
        self.assertEqual(result.status, "action_failed")
        self.assertEqual(self.audit.events[-1]["outcome"], "failed")
        self.assertEqual(self.store.completed[-1][-1], "action_failed")

    async def test_policy_failure_fails_closed(self):
        class BrokenPolicy:
            async def decide(self, event):
                raise RuntimeError("policy store unavailable")
        service = RuntimeSecurityAutoResponder(
            detector=self.detector, policy=BrokenPolicy(), executor=self.executor,
            audit=self.audit, event_store=self.store,
        )
        result = await service.handle(self.observation)
        self.assertEqual(result.status, "decision_failed")
        self.assertEqual(self.actions, [])

    async def test_invalid_observation_and_unsafe_handler_registration_rejected(self):
        with self.assertRaises(ValueError):
            RuntimeSecurityObservation(
                observation_id="", tenant_id="tenant-a", run_id="run-a",
                actor_id="agent-a", event_type="credential_anomaly",
                signal="failed_auth", observed_value=9, occurred_at="now",
            )
        with self.assertRaises(ValueError):
            SecurityResponseExecutor({"shell": self.executor.execute})


if __name__ == "__main__":
    unittest.main()
