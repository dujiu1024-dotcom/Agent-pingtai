"""Cross-asset AI supply-chain attestation and security admission policy.

Cryptographic signature verification, publisher identity lookup and build provenance
verification are delegated to existing trusted services through explicit adapters.
This module owns the normalized evidence contract and fail-closed admission decision;
it does not implement a second publisher registry, credential store or sandbox.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from uuid import uuid4


class ArtifactKind(str, Enum):
    MODEL = "model"
    AGENT = "agent"
    PROMPT = "prompt"
    SKILL = "skill"
    TOOL = "tool"
    MCP_SERVER = "mcp_server"
    CONNECTOR = "connector"
    WORKFLOW = "workflow"
    IMAGE = "image"
    PACKAGE = "package"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AdmissionDecision(str, Enum):
    ALLOW = "allow"
    QUARANTINE = "quarantine"
    BLOCK = "block"


@dataclass(frozen=True)
class PublisherEvidence:
    publisher_id: str
    issuer: str
    verified: bool
    identity_ref: str

    def __post_init__(self):
        for name in ("publisher_id", "issuer", "identity_ref"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")


@dataclass(frozen=True)
class BuildProvenance:
    repository_url: str
    commit_sha: str
    build_id: str
    builder_id: str
    verified: bool

    def __post_init__(self):
        if not self.repository_url.startswith(("https://", "ssh://", "git@")):
            raise ValueError("repository_url must identify a remote source repository")
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", self.commit_sha):
            raise ValueError("commit_sha must be a hexadecimal commit identifier")
        for name in ("build_id", "builder_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")


@dataclass(frozen=True)
class SignatureEvidence:
    algorithm: str
    key_id: str
    signature_ref: str
    verified: bool

    def __post_init__(self):
        if self.algorithm.lower() not in {"ed25519", "ecdsa-p256-sha256", "rsa-pss-sha256"}:
            raise ValueError("unsupported artifact signature algorithm")
        for name in ("key_id", "signature_ref"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")


@dataclass(frozen=True)
class SBOMComponent:
    name: str
    version: str
    purl: str
    supplier: str
    license_id: str

    def __post_init__(self):
        for name in ("name", "version", "purl", "supplier", "license_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"SBOM component {name} is required")
        if not self.purl.startswith("pkg:"):
            raise ValueError("SBOM component purl must use package-url format")


@dataclass(frozen=True)
class SecurityFinding:
    code: str
    severity: Severity
    message: str
    component_purl: str | None = None
    evidence_ref: str | None = None

    def __post_init__(self):
        if not self.code.strip() or not self.message.strip():
            raise ValueError("finding code and message are required")


@dataclass(frozen=True)
class SupplyChainPolicy:
    allowed_publishers: frozenset[str]
    allowed_repository_hosts: frozenset[str]
    allowed_licenses: frozenset[str]
    max_vulnerability_severity: Severity = Severity.HIGH
    max_declared_permissions: frozenset[str] = frozenset({
        "filesystem.read", "network.egress", "secrets.read", "tools.invoke",
    })
    block_unknown_license: bool = True
    quarantine_on_high_risk: bool = True


@dataclass(frozen=True)
class ArtifactAttestation:
    tenant_id: str
    artifact_id: str
    artifact_kind: ArtifactKind
    version: str
    content_digest: str
    content: bytes = field(repr=False, compare=False)
    publisher: PublisherEvidence | None = None
    provenance: BuildProvenance | None = None
    signature: SignatureEvidence | None = None
    sbom_format: str = "CycloneDX"
    sbom_version: str = "1.5"
    components: tuple[SBOMComponent, ...] = ()
    declared_permissions: frozenset[str] = frozenset()
    findings: tuple[SecurityFinding, ...] = ()
    license_expression: str = "NOASSERTION"

    def __post_init__(self):
        for name in ("tenant_id", "artifact_id", "version"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_digest):
            raise ValueError("content_digest must be a canonical sha256 digest")
        if hashlib.sha256(self.content).hexdigest() != self.content_digest[7:]:
            raise ValueError("artifact content digest mismatch")
        if self.sbom_format not in {"CycloneDX", "SPDX"}:
            raise ValueError("SBOM format must be CycloneDX or SPDX")
        if not self.sbom_version.strip():
            raise ValueError("SBOM version is required")


@dataclass(frozen=True)
class VerificationResult:
    publisher_verified: bool
    provenance_verified: bool
    signature_verified: bool


class SupplyChainVerifier(Protocol):
    async def verify(self, evidence: ArtifactAttestation) -> VerificationResult: ...


@dataclass(frozen=True)
class AdmissionReport:
    tenant_id: str
    artifact_id: str
    version: str
    decision: AdmissionDecision
    risk_score: int
    findings: tuple[SecurityFinding, ...]
    evidence_digest: str
    report_id: str = field(default_factory=lambda: str(uuid4()))


class SupplyChainAdmissionService:
    _severity_rank = {
        Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2,
        Severity.HIGH: 3, Severity.CRITICAL: 4,
    }

    def __init__(self, *, verifier: SupplyChainVerifier, policy: SupplyChainPolicy):
        self.verifier, self.policy = verifier, policy

    async def evaluate(self, artifact: ArtifactAttestation) -> AdmissionReport:
        findings = list(artifact.findings)
        verified = await self.verifier.verify(artifact)

        if artifact.publisher is None:
            findings.append(SecurityFinding("PROVENANCE_PUBLISHER_MISSING", Severity.CRITICAL,
                "publisher identity evidence is missing"))
        elif not verified.publisher_verified or not artifact.publisher.verified:
            findings.append(SecurityFinding("PROVENANCE_PUBLISHER_UNVERIFIED", Severity.CRITICAL,
                "publisher identity could not be verified"))
        elif artifact.publisher.publisher_id not in self.policy.allowed_publishers:
            findings.append(SecurityFinding("PUBLISHER_NOT_TRUSTED", Severity.HIGH,
                "publisher is not in the tenant trust policy"))

        if artifact.provenance is None:
            findings.append(SecurityFinding("BUILD_PROVENANCE_MISSING", Severity.CRITICAL,
                "source repository and build provenance are missing"))
        else:
            host = self._repository_host(artifact.provenance.repository_url)
            if not verified.provenance_verified or not artifact.provenance.verified:
                findings.append(SecurityFinding("BUILD_PROVENANCE_INVALID", Severity.CRITICAL,
                    "build provenance verification failed"))
            if host not in self.policy.allowed_repository_hosts:
                findings.append(SecurityFinding("SOURCE_REPOSITORY_UNTRUSTED", Severity.HIGH,
                    "source repository host is not allowlisted"))

        if artifact.signature is None:
            findings.append(SecurityFinding("SIGNATURE_MISSING", Severity.CRITICAL,
                "artifact signature evidence is missing"))
        elif not verified.signature_verified or not artifact.signature.verified:
            findings.append(SecurityFinding("SIGNATURE_INVALID", Severity.CRITICAL,
                "artifact signature verification failed"))

        if not artifact.components:
            findings.append(SecurityFinding("SBOM_MISSING", Severity.CRITICAL,
                "SBOM has no dependency components"))
        else:
            seen: set[str] = set()
            for component in artifact.components:
                if component.purl in seen:
                    findings.append(SecurityFinding("SBOM_DUPLICATE_COMPONENT", Severity.MEDIUM,
                        "SBOM contains duplicate component identity", component.purl))
                seen.add(component.purl)
                if component.license_id not in self.policy.allowed_licenses:
                    severity = Severity.HIGH if self.policy.block_unknown_license or component.license_id != "NOASSERTION" else Severity.MEDIUM
                    findings.append(SecurityFinding("LICENSE_NOT_ALLOWED", severity,
                        f"component license {component.license_id} is not approved", component.purl))
                # A package claiming a public namespace but supplied by a different
                # publisher is flagged for dependency-confusion review.
                if component.purl.startswith("pkg:npm/") and component.supplier.lower() not in {
                    "npm", "github", "verified", "internal",
                }:
                    findings.append(SecurityFinding("DEPENDENCY_SUPPLIER_MISMATCH", Severity.HIGH,
                        "package namespace and declared supplier are inconsistent", component.purl))

        excessive = artifact.declared_permissions - self.policy.max_declared_permissions
        for permission in sorted(excessive):
            findings.append(SecurityFinding("PERMISSION_OVERBROAD", Severity.HIGH,
                f"declared permission exceeds policy: {permission}"))

        # Scanner-originated malicious-behaviour, data-exfiltration and CVE
        # findings are preserved and evaluated alongside provenance findings.
        max_allowed = self._severity_rank[self.policy.max_vulnerability_severity]
        critical = any(f.severity is Severity.CRITICAL for f in findings)
        over_threshold = any(
            self._severity_rank[f.severity] > max_allowed
            and f.code.startswith(("CVE-", "VULNERABILITY"))
            for f in findings
        )
        malicious = any(f.code.startswith(("MALWARE", "MALICIOUS", "DATA_EXFILTRATION")) for f in findings)
        if critical or malicious or over_threshold:
            decision = AdmissionDecision.BLOCK
        elif any(f.severity in {Severity.HIGH, Severity.CRITICAL} for f in findings):
            decision = AdmissionDecision.QUARANTINE if self.policy.quarantine_on_high_risk else AdmissionDecision.BLOCK
        elif findings:
            decision = AdmissionDecision.QUARANTINE
        else:
            decision = AdmissionDecision.ALLOW

        weights = {Severity.INFO: 0, Severity.LOW: 5, Severity.MEDIUM: 15,
                   Severity.HIGH: 30, Severity.CRITICAL: 50}
        risk = min(100, sum(weights[f.severity] for f in findings))
        evidence_digest = hashlib.sha256(
            (artifact.content_digest + "|" + "|".join(sorted(f.code for f in findings))).encode()
        ).hexdigest()
        return AdmissionReport(artifact.tenant_id, artifact.artifact_id, artifact.version,
            decision, risk, tuple(findings), f"sha256:{evidence_digest}")

    @staticmethod
    def _repository_host(url: str) -> str:
        if url.startswith("git@"):
            return url[4:].split(":", 1)[0].lower()
        match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/:]+)", url)
        return match.group(1).lower() if match else ""
