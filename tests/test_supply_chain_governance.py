from __future__ import annotations

import unittest
from datetime import datetime

from agent_platform.supply_chain.admission import AdmissionDecision, AdmissionReport, Severity
from agent_platform.supply_chain.governance import (
    ApprovalResult, ApprovalStatus, EnterpriseAdmissionOrchestrator,
    EnterpriseSupplyChainPolicy, LifecycleState, RiskLevel, RuntimeAction,
    RuntimeGovernanceMonitor, RuntimeObservation,
)


class Approval:
    def __init__(self, status=ApprovalStatus.APPROVED):
        self.status, self.requests = status, []

    async def request(self, request):
        self.requests.append(request)
        return ApprovalResult(self.status, "approval-1", "approver-1")


class Sandbox:
    def __init__(self):
        self.calls = []

    async def start_trial(self, **kwargs):
        self.calls.append(kwargs)
        return "sandbox-trial-1"


class Audit:
    def __init__(self):
        self.events = []

    async def append(self, **kwargs):
        self.events.append(kwargs)


class Controls:
    def __init__(self):
        self.calls = []

    async def apply(self, **kwargs):
        self.calls.append(kwargs)


class Incidents:
    def __init__(self):
        self.items = []

    async def record(self, incident):
        self.items.append(incident)


def report(tenant="tenant-a", score=5, findings=(), decision=AdmissionDecision.ALLOW):
    return AdmissionReport(tenant, "skill-42", "1.0.0", decision, score, tuple(findings), "sha256:evidence")


def policy(**kwargs):
    values = dict(tenant_id="tenant-a", policy_id="corp-default", version=3,
                  approval_required_at=RiskLevel.MEDIUM,
                  sandbox_required_at=RiskLevel.LOW, trial_required_at=RiskLevel.MEDIUM)
    values.update(kwargs)
    return EnterpriseSupplyChainPolicy(**values)


class EnterpriseAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_low_risk_internal_asset_activates_without_approval(self):
        approvals, sandbox, audit = Approval(), Sandbox(), Audit()
        service = EnterpriseAdmissionOrchestrator(policy=policy(), approvals=approvals,
                                                   sandbox=sandbox, audit=audit)
        result = await service.admit(report(), publisher_is_external=False,
                                     permissions=frozenset({"tools.invoke"}))
        self.assertEqual(result.state, LifecycleState.ACTIVE)
        self.assertEqual(approvals.requests, [])
        self.assertEqual(sandbox.calls, [])
        self.assertEqual(audit.events[0]["event_type"], "supply_chain.admission_activated")

    async def test_external_publisher_requires_approval_and_trial(self):
        approvals, sandbox, audit = Approval(), Sandbox(), Audit()
        service = EnterpriseAdmissionOrchestrator(policy=policy(), approvals=approvals,
                                                   sandbox=sandbox, audit=audit)
        result = await service.admit(report(score=25), publisher_is_external=True,
                                     permissions=frozenset({"network.egress"}))
        self.assertEqual(result.state, LifecycleState.TRIAL)
        self.assertEqual(len(approvals.requests), 1)
        self.assertTrue(sandbox.calls[0]["network_isolated"])
        self.assertEqual(result.sandbox_id, "sandbox-trial-1")

    async def test_trial_must_pass_before_activation(self):
        service = EnterpriseAdmissionOrchestrator(policy=policy(), approvals=Approval(),
                                                   sandbox=Sandbox(), audit=Audit())
        trial = await service.admit(report(score=25), publisher_is_external=True,
                                    permissions=frozenset())
        passed = await service.complete_trial(trial, sandbox_passed=True,
                                              evidence_digest="sha256:trial-pass")
        failed = await service.complete_trial(trial, sandbox_passed=False,
                                              evidence_digest="sha256:trial-fail")
        self.assertEqual(passed.state, LifecycleState.ACTIVE)
        self.assertEqual(failed.state, LifecycleState.QUARANTINED)

    async def test_trial_completion_rejects_non_trial_state(self):
        service = EnterpriseAdmissionOrchestrator(policy=policy(), approvals=Approval(),
                                                   sandbox=Sandbox(), audit=Audit())
        active = await service.admit(report(), publisher_is_external=False,
                                     permissions=frozenset())
        with self.assertRaises(ValueError):
            await service.complete_trial(active, sandbox_passed=True,
                                         evidence_digest="sha256:invalid")

    async def test_pending_or_rejected_approval_prevents_sandbox(self):
        for status, expected in ((ApprovalStatus.PENDING, LifecycleState.PENDING_APPROVAL),
                                 (ApprovalStatus.REJECTED, LifecycleState.REJECTED)):
            approvals, sandbox = Approval(status), Sandbox()
            service = EnterpriseAdmissionOrchestrator(policy=policy(), approvals=approvals,
                                                       sandbox=sandbox, audit=Audit())
            result = await service.admit(report(score=40), publisher_is_external=True,
                                         permissions=frozenset())
            self.assertEqual(result.state, expected)
            self.assertEqual(sandbox.calls, [])

    async def test_blocked_security_report_is_rejected_without_external_actions(self):
        approvals, sandbox = Approval(), Sandbox()
        service = EnterpriseAdmissionOrchestrator(policy=policy(), approvals=approvals,
                                                   sandbox=sandbox, audit=Audit())
        result = await service.admit(report(decision=AdmissionDecision.BLOCK),
                                     publisher_is_external=False, permissions=frozenset())
        self.assertEqual(result.state, LifecycleState.REJECTED)
        self.assertFalse(approvals.requests)
        self.assertFalse(sandbox.calls)

    async def test_cross_tenant_report_is_rejected(self):
        service = EnterpriseAdmissionOrchestrator(policy=policy(), approvals=Approval(),
                                                   sandbox=Sandbox(), audit=Audit())
        with self.assertRaises(PermissionError):
            await service.admit(report(tenant="tenant-b"), publisher_is_external=False,
                                permissions=frozenset())

    async def test_policy_validation(self):
        with self.assertRaises(ValueError):
            policy(max_auto_admit_risk=101)


class RuntimeGovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_high_and_critical_observations_trigger_containment(self):
        controls, incidents, audit = Controls(), Incidents(), Audit()
        monitor = RuntimeGovernanceMonitor(policy=policy(), controls=controls,
                                           incidents=incidents, audit=audit)
        event = RuntimeObservation("tenant-a", "agent-1", "2.0", "instance-9",
                                   "secret_exfiltration", Severity.CRITICAL,
                                   "secret sent to untrusted endpoint",
                                   {"destination": "external"})
        incident = await monitor.observe(event)
        self.assertEqual(incident.action, RuntimeAction.REVOKE)
        self.assertEqual(controls.calls[0]["action"], RuntimeAction.REVOKE)
        self.assertEqual(len(incidents.items), 1)
        self.assertEqual(audit.events[0]["event_type"], "supply_chain.runtime_incident")
        self.assertTrue(incident.evidence_digest.startswith("sha256:"))

    async def test_medium_severity_quarantines_and_low_alerts(self):
        monitor = RuntimeGovernanceMonitor(policy=policy(), controls=Controls(),
                                           incidents=Incidents(), audit=Audit())
        medium = await monitor.observe(RuntimeObservation(
            "tenant-a", "tool-1", "1", "i-1", "policy_violation", Severity.MEDIUM, "policy mismatch"))
        low = await monitor.observe(RuntimeObservation(
            "tenant-a", "tool-1", "1", "i-1", "anomaly", Severity.LOW, "unusual call rate"))
        self.assertEqual(medium.action, RuntimeAction.QUARANTINE)
        self.assertEqual(low.action, RuntimeAction.ALERT)

    async def test_cross_tenant_runtime_event_is_rejected(self):
        monitor = RuntimeGovernanceMonitor(policy=policy(), controls=Controls(),
                                           incidents=Incidents(), audit=Audit())
        with self.assertRaises(PermissionError):
            await monitor.observe(RuntimeObservation(
                "tenant-b", "tool-1", "1", "i-1", "tamper", Severity.CRITICAL, "digest mismatch"))

    async def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            RuntimeObservation("tenant-a", "tool-1", "1", "i-1", "event",
                               Severity.LOW, "description", observed_at=datetime.now())


if __name__ == "__main__":
    unittest.main()
