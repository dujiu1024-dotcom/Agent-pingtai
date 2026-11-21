"""Enterprise admission lifecycle and runtime governance for AI supply-chain assets.

This layer composes the L2-T07 admission report with existing approval, sandbox,
audit and asset-control services through adapters. It intentionally owns policy
decisions and event orchestration, not duplicate identity or sandbox primitives.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol
from uuid import uuid4

from agent_platform.supply_chain.admission import AdmissionDecision, AdmissionReport, Severity


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LifecycleState(str, Enum):
    REJECTED = "rejected"
    PENDING_APPROVAL = "pending_approval"
    QUARANTINED = "quarantined"
    TRIAL = "trial"
    ACTIVE = "active"


class RuntimeAction(str, Enum):
    ALERT = "alert"
    QUARANTINE = "quarantine"
    FREEZE = "freeze"
    REVOKE = "revoke"
    DISABLE_VERSION = "disable_version"
    ROLLBACK = "rollback"


class ApprovalStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"


@dataclass(frozen=True)
class EnterpriseSupplyChainPolicy:
    tenant_id: str
    policy_id: str
    version: int
    max_auto_admit_risk: int = 10
    approval_required_at: RiskLevel = RiskLevel.MEDIUM
    sandbox_required_at: RiskLevel = RiskLevel.LOW
    trial_required_at: RiskLevel = RiskLevel.MEDIUM
    max_trial_minutes: int = 1440
    runtime_action_by_severity: Mapping[Severity, RuntimeAction] = field(default_factory=lambda: {
        Severity.INFO: RuntimeAction.ALERT,
        Severity.LOW: RuntimeAction.ALERT,
        Severity.MEDIUM: RuntimeAction.QUARANTINE,
        Severity.HIGH: RuntimeAction.FREEZE,
        Severity.CRITICAL: RuntimeAction.REVOKE,
    })
    approval_required_for_external_publishers: bool = True

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.policy_id.strip():
            raise ValueError("tenant_id and policy_id are required")
        if self.version < 1 or not 0 <= self.max_auto_admit_risk <= 100:
            raise ValueError("invalid policy version or risk threshold")
        if self.max_trial_minutes < 1:
            raise ValueError("max_trial_minutes must be positive")


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    tenant_id: str
    artifact_id: str
    version: str
    risk_level: RiskLevel
    risk_score: int
    policy_id: str
    evidence_digest: str
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ApprovalResult:
    status: ApprovalStatus
    approval_id: str | None = None
    approver_id: str | None = None
    reason: str | None = None


class ApprovalGateway(Protocol):
    async def request(self, request: ApprovalRequest) -> ApprovalResult: ...


class SandboxGateway(Protocol):
    async def start_trial(self, *, tenant_id: str, artifact_id: str, version: str,
                          max_duration_minutes: int, network_isolated: bool,
                          permissions: frozenset[str]) -> str: ...


class AuditSink(Protocol):
    async def append(self, *, tenant_id: str, event_type: str, payload: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True)
class AdmissionLifecycleResult:
    tenant_id: str
    artifact_id: str
    version: str
    state: LifecycleState
    risk_level: RiskLevel
    approval: ApprovalResult | None = None
    sandbox_id: str | None = None
    reason: str = ""


class EnterpriseAdmissionOrchestrator:
    def __init__(self, *, policy: EnterpriseSupplyChainPolicy,
                 approvals: ApprovalGateway, sandbox: SandboxGateway,
                 audit: AuditSink):
        self.policy, self.approvals, self.sandbox, self.audit = policy, approvals, sandbox, audit

    async def admit(self, report: AdmissionReport, *, publisher_is_external: bool,
                    permissions: frozenset[str]) -> AdmissionLifecycleResult:
        if report.tenant_id != self.policy.tenant_id:
            raise PermissionError("admission report tenant does not match policy tenant")
        risk = self._risk_level(report.risk_score, report.findings)
        if report.decision is AdmissionDecision.BLOCK:
            result = AdmissionLifecycleResult(report.tenant_id, report.artifact_id, report.version,
                LifecycleState.REJECTED, risk, reason="upstream security admission blocked")
            await self._audit("supply_chain.admission_rejected", result, report.evidence_digest)
            return result
        needs_approval = (
            self._risk_rank(risk) >= self._risk_rank(self.policy.approval_required_at)
            or (publisher_is_external and self.policy.approval_required_for_external_publishers)
        )
        approval: ApprovalResult | None = None
        if needs_approval:
            request = ApprovalRequest(str(uuid4()), report.tenant_id, report.artifact_id,
                report.version, risk, report.risk_score, self.policy.policy_id, report.evidence_digest)
            approval = await self.approvals.request(request)
            if approval.status is ApprovalStatus.REJECTED:
                result = AdmissionLifecycleResult(report.tenant_id, report.artifact_id, report.version,
                    LifecycleState.REJECTED, risk, approval, reason="enterprise approval rejected")
                await self._audit("supply_chain.approval_rejected", result, report.evidence_digest)
                return result
            if approval.status is ApprovalStatus.PENDING:
                result = AdmissionLifecycleResult(report.tenant_id, report.artifact_id, report.version,
                    LifecycleState.PENDING_APPROVAL, risk, approval, reason="awaiting authorized approver")
                await self._audit("supply_chain.approval_requested", result, report.evidence_digest)
                return result
        if self._risk_rank(risk) >= self._risk_rank(self.policy.sandbox_required_at):
            sandbox_id = await self.sandbox.start_trial(
                tenant_id=report.tenant_id, artifact_id=report.artifact_id, version=report.version,
                max_duration_minutes=self.policy.max_trial_minutes, network_isolated=True,
                permissions=permissions,
            )
            result = AdmissionLifecycleResult(report.tenant_id, report.artifact_id, report.version,
                LifecycleState.TRIAL, risk, approval, sandbox_id, "isolated trial required before activation")
            await self._audit("supply_chain.trial_started", result, report.evidence_digest)
            return result
        result = AdmissionLifecycleResult(report.tenant_id, report.artifact_id, report.version,
            LifecycleState.ACTIVE, risk, approval, reason="policy permits activation")
        await self._audit("supply_chain.admission_activated", result, report.evidence_digest)
        return result

    async def _audit(self, event_type: str, result: AdmissionLifecycleResult, evidence_digest: str) -> None:
        await self.audit.append(tenant_id=result.tenant_id, event_type=event_type, payload={
            "artifact_id": result.artifact_id, "version": result.version,
            "state": result.state.value, "risk_level": result.risk_level.value,
            "policy_id": self.policy.policy_id, "policy_version": self.policy.version,
            "evidence_digest": evidence_digest, "reason": result.reason,
            "approval_id": result.approval.approval_id if result.approval else None,
            "sandbox_id": result.sandbox_id,
        })

    @staticmethod
    def _risk_rank(level: RiskLevel) -> int:
        return {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}[level]

    @staticmethod
    def _risk_level(score: int, findings: tuple[Any, ...]) -> RiskLevel:
        if any(item.severity is Severity.CRITICAL for item in findings):
            return RiskLevel.CRITICAL
        if score >= 60 or any(item.severity is Severity.HIGH for item in findings):
            return RiskLevel.HIGH
        if score >= 20 or any(item.severity is Severity.MEDIUM for item in findings):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


@dataclass(frozen=True)
class RuntimeObservation:
    tenant_id: str
    artifact_id: str
    version: str
    instance_id: str
    event_type: str
    severity: Severity
    description: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for name in ("tenant_id", "artifact_id", "version", "instance_id", "event_type", "description"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True)
class RuntimeIncident:
    incident_id: str
    tenant_id: str
    artifact_id: str
    version: str
    instance_id: str
    severity: Severity
    action: RuntimeAction
    event_type: str
    evidence_digest: str
    observed_at: datetime


class RuntimeControlGateway(Protocol):
    async def apply(self, *, tenant_id: str, artifact_id: str, version: str,
                    instance_id: str, action: RuntimeAction, reason: str) -> None: ...


class RuntimeIncidentStore(Protocol):
    async def record(self, incident: RuntimeIncident) -> None: ...


class RuntimeGovernanceMonitor:
    """Maps runtime observations to tenant policy actions and durable incident records."""

    def __init__(self, *, policy: EnterpriseSupplyChainPolicy,
                 controls: RuntimeControlGateway, incidents: RuntimeIncidentStore,
                 audit: AuditSink):
        self.policy, self.controls, self.incidents, self.audit = policy, controls, incidents, audit

    async def observe(self, event: RuntimeObservation) -> RuntimeIncident:
        if event.tenant_id != self.policy.tenant_id:
            raise PermissionError("runtime event tenant does not match policy tenant")
        action = self.policy.runtime_action_by_severity.get(event.severity, RuntimeAction.REVOKE)
        canonical = json.dumps({
            "tenant_id": event.tenant_id, "artifact_id": event.artifact_id,
            "version": event.version, "instance_id": event.instance_id,
            "event_type": event.event_type, "severity": event.severity.value,
            "description": event.description, "evidence": dict(event.evidence),
            "observed_at": event.observed_at.isoformat(),
        }, sort_keys=True, separators=(",", ":"), default=str)
        digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        incident = RuntimeIncident(str(uuid4()), event.tenant_id, event.artifact_id,
            event.version, event.instance_id, event.severity, action, event.event_type,
            digest, event.observed_at)
        # Apply containment first. If the control plane fails, do not mark the
        # incident as successfully handled; the caller receives the adapter error.
        await self.controls.apply(tenant_id=event.tenant_id, artifact_id=event.artifact_id,
            version=event.version, instance_id=event.instance_id, action=action,
            reason=f"{event.event_type}: {event.description}")
        await self.incidents.record(incident)
        await self.audit.append(tenant_id=event.tenant_id, event_type="supply_chain.runtime_incident",
            payload={"incident_id": incident.incident_id, "artifact_id": event.artifact_id,
                "version": event.version, "instance_id": event.instance_id,
                "severity": event.severity.value, "action": action.value,
                "event_type": event.event_type, "evidence_digest": digest})
        return incident
