"""Tenant-scoped supply-chain admission API; scanner/verifier adapters are host-owned."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.supply_chain.admission import (
    ArtifactAttestation, ArtifactKind, BuildProvenance, PublisherEvidence,
    SBOMComponent, SecurityFinding, Severity, SignatureEvidence,
    SupplyChainAdmissionService,
)

PrincipalResolver = Callable[[Request], Awaitable[Any]]


def create_supply_chain_router(*, service: SupplyChainAdmissionService,
                               resolve_principal: PrincipalResolver,
                               authorize_manage: Callable[[Any], Awaitable[bool]]) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/supply-chain", tags=["supply-chain"])

    @router.post("/admission/evaluate")
    async def evaluate(request: Request) -> dict[str, Any]:
        principal = await resolve_principal(request)
        if principal is None:
            raise HTTPException(401, "authenticated principal required")
        if not await authorize_manage(principal):
            raise HTTPException(403, "supply-chain admission permission denied")
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON object required")
        try:
            content = bytes.fromhex(payload["content_hex"])
            publisher_data = payload.get("publisher")
            provenance_data = payload.get("provenance")
            signature_data = payload.get("signature")
            publisher = PublisherEvidence(**publisher_data) if publisher_data else None
            provenance = BuildProvenance(**provenance_data) if provenance_data else None
            signature = SignatureEvidence(**signature_data) if signature_data else None
            components = tuple(SBOMComponent(**item) for item in payload.get("components", []))
            findings = tuple(SecurityFinding(**{
                **item, "severity": Severity(item["severity"])
            }) for item in payload.get("findings", []))
            artifact = ArtifactAttestation(
                tenant_id=principal.tenant_id,
                artifact_id=payload["artifact_id"],
                artifact_kind=ArtifactKind(payload["artifact_kind"]),
                version=payload["version"],
                content_digest=payload["content_digest"],
                content=content,
                publisher=publisher,
                provenance=provenance,
                signature=signature,
                sbom_format=payload.get("sbom_format", "CycloneDX"),
                sbom_version=payload.get("sbom_version", "1.5"),
                components=components,
                declared_permissions=frozenset(payload.get("declared_permissions", [])),
                findings=findings,
                license_expression=payload.get("license_expression", "NOASSERTION"),
            )
            report = await service.evaluate(artifact)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "report_id": report.report_id,
            "tenant_id": report.tenant_id,
            "artifact_id": report.artifact_id,
            "version": report.version,
            "decision": report.decision.value,
            "risk_score": report.risk_score,
            "evidence_digest": report.evidence_digest,
            "findings": [{
                "code": item.code, "severity": item.severity.value,
                "message": item.message, "component_purl": item.component_purl,
                "evidence_ref": item.evidence_ref,
            } for item in report.findings],
        }

    return router
