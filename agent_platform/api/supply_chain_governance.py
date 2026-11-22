"""Tenant-bound API for enterprise supply-chain lifecycle and runtime observations."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.supply_chain.admission import Severity
from agent_platform.supply_chain.governance import (
    EnterpriseAdmissionOrchestrator,
    RuntimeGovernanceMonitor,
    RuntimeObservation,
)

PrincipalResolver = Callable[[Request], Awaitable[Any]]
AdmissionResolver = Callable[[Any], Awaitable[EnterpriseAdmissionOrchestrator]]
RuntimeResolver = Callable[[Any], Awaitable[RuntimeGovernanceMonitor]]
ManageAuthorizer = Callable[[Any], Awaitable[bool]]


def create_supply_chain_governance_router(
    *, resolve_principal: PrincipalResolver,
    resolve_admission: AdmissionResolver,
    resolve_runtime: RuntimeResolver,
    authorize_manage: ManageAuthorizer,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/supply-chain", tags=["supply-chain-governance"])

    async def principal_for(request: Request) -> Any:
        principal = await resolve_principal(request)
        if principal is None:
            raise HTTPException(401, "authenticated principal required")
        if not await authorize_manage(principal):
            raise HTTPException(403, "supply-chain management permission required")
        return principal

    @router.post("/governance/admit")
    async def admit(request: Request) -> dict[str, Any]:
        principal = await principal_for(request)
        payload = await request.json()
        report = payload.get("admission_report")
        if not isinstance(report, dict):
            raise HTTPException(422, "admission_report object is required")
        # Admission report is produced by the trusted L2-T07 service, not accepted
        # as an arbitrary caller assertion. Resolve it by ID from the host service.
        resolver = getattr(request.app.state, "supply_chain_report_resolver", None)
        if resolver is None:
            raise HTTPException(503, "trusted admission report resolver is not configured")
        trusted_report = await resolver(principal, str(report.get("report_id", "")))
        if trusted_report is None:
            raise HTTPException(404, "admission report not found")
        service = await resolve_admission(principal)
        if service.policy.tenant_id != str(principal.tenant_id):
            raise HTTPException(403, "tenant policy scope mismatch")
        context_resolver = getattr(request.app.state, "supply_chain_asset_context_resolver", None)
        if context_resolver is None:
            raise HTTPException(503, "trusted asset context resolver is not configured")
        asset_context = await context_resolver(principal, trusted_report.artifact_id, trusted_report.version)
        if asset_context is None:
            raise HTTPException(404, "trusted asset context not found")
        result = await service.admit(
            trusted_report,
            publisher_is_external=bool(asset_context.get("publisher_is_external", True)),
            permissions=frozenset(str(item) for item in asset_context.get("permissions", [])),
        )
        return {
            "tenant_id": result.tenant_id, "artifact_id": result.artifact_id,
            "version": result.version, "state": result.state.value,
            "risk_level": result.risk_level.value, "reason": result.reason,
            "approval": ({
                "status": result.approval.status.value,
                "approval_id": result.approval.approval_id,
                "approver_id": result.approval.approver_id,
            } if result.approval else None),
            "sandbox_id": result.sandbox_id,
        }

    @router.post("/governance/trials/{sandbox_id}/complete")
    async def complete_trial(sandbox_id: str, request: Request) -> dict[str, Any]:
        principal = await principal_for(request)
        service = await resolve_admission(principal)
        if service.policy.tenant_id != str(principal.tenant_id):
            raise HTTPException(403, "tenant policy scope mismatch")
        trial_resolver = getattr(request.app.state, "supply_chain_trial_resolver", None)
        verdict_resolver = getattr(request.app.state, "supply_chain_trial_verdict_resolver", None)
        if trial_resolver is None or verdict_resolver is None:
            raise HTTPException(503, "trusted sandbox trial resolvers are not configured")
        trial = await trial_resolver(principal, sandbox_id)
        if trial is None:
            raise HTTPException(404, "sandbox trial not found")
        passed, evidence_digest = await verdict_resolver(principal, sandbox_id)
        result = await service.complete_trial(
            trial, sandbox_passed=bool(passed), evidence_digest=str(evidence_digest),
        )
        return {
            "tenant_id": result.tenant_id, "artifact_id": result.artifact_id,
            "version": result.version, "state": result.state.value,
            "sandbox_id": result.sandbox_id, "reason": result.reason,
        }

    @router.post("/runtime/observations")
    async def observe(request: Request) -> dict[str, Any]:
        principal = await principal_for(request)
        payload = await request.json()
        try:
            severity = Severity(str(payload["severity"]).lower())
            event = RuntimeObservation(
                tenant_id=str(principal.tenant_id),
                artifact_id=str(payload["artifact_id"]),
                version=str(payload["version"]),
                instance_id=str(payload["instance_id"]),
                event_type=str(payload["event_type"]),
                severity=severity,
                description=str(payload["description"]),
                evidence=payload.get("evidence", {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"invalid runtime observation: {exc}") from exc
        monitor = await resolve_runtime(principal)
        if monitor.policy.tenant_id != str(principal.tenant_id):
            raise HTTPException(403, "tenant policy scope mismatch")
        incident = await monitor.observe(event)
        return {
            "incident_id": incident.incident_id, "tenant_id": incident.tenant_id,
            "artifact_id": incident.artifact_id, "version": incident.version,
            "instance_id": incident.instance_id, "severity": incident.severity.value,
            "action": incident.action.value, "evidence_digest": incident.evidence_digest,
        }

    return router
