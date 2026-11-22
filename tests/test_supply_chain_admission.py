from __future__ import annotations

import hashlib
import unittest

from agent_platform.supply_chain.admission import (
    AdmissionDecision, ArtifactAttestation, ArtifactKind, BuildProvenance,
    PublisherEvidence, SBOMComponent, SecurityFinding, Severity,
    SignatureEvidence, SupplyChainAdmissionService, SupplyChainPolicy,
    VerificationResult,
)


class Verifier:
    def __init__(self, result=None):
        self.result = result or VerificationResult(True, True, True)
        self.calls = 0

    async def verify(self, evidence):
        self.calls += 1
        return self.result


def artifact(**overrides):
    data = b"trusted-model-artifact"
    values = dict(
        tenant_id="tenant-a", artifact_id="model-1", artifact_kind=ArtifactKind.MODEL,
        version="1.2.3", content_digest="sha256:" + hashlib.sha256(data).hexdigest(),
        content=data,
        publisher=PublisherEvidence("publisher-internal", "corp-idp", True, "identity://pub/1"),
        provenance=BuildProvenance("https://github.com/acme/model", "a" * 40, "build-42", "ci://trusted", True),
        signature=SignatureEvidence("ed25519", "key-1", "sigstore://artifact/1", True),
        components=(SBOMComponent("runtime", "2.1", "pkg:pypi/runtime@2.1", "internal", "MIT"),),
        declared_permissions=frozenset({"filesystem.read", "tools.invoke"}),
    )
    values.update(overrides)
    return ArtifactAttestation(**values)


def policy():
    return SupplyChainPolicy(
        allowed_publishers=frozenset({"publisher-internal"}),
        allowed_repository_hosts=frozenset({"github.com"}),
        allowed_licenses=frozenset({"MIT", "Apache-2.0"}),
    )


class SupplyChainAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_attestation_sbom_and_policy_admit(self):
        verifier = Verifier()
        report = await SupplyChainAdmissionService(verifier=verifier, policy=policy()).evaluate(artifact())
        self.assertEqual(report.decision, AdmissionDecision.ALLOW)
        self.assertEqual(report.risk_score, 0)
        self.assertEqual(verifier.calls, 1)

    async def test_missing_publisher_signature_provenance_and_sbom_fail_closed(self):
        item = artifact(publisher=None, provenance=None, signature=None, components=())
        report = await SupplyChainAdmissionService(verifier=Verifier(), policy=policy()).evaluate(item)
        self.assertEqual(report.decision, AdmissionDecision.BLOCK)
        codes = {finding.code for finding in report.findings}
        self.assertTrue({"PROVENANCE_PUBLISHER_MISSING", "BUILD_PROVENANCE_MISSING",
                         "SIGNATURE_MISSING", "SBOM_MISSING"} <= codes)

    async def test_invalid_signature_blocks_even_if_other_evidence_is_present(self):
        verifier = Verifier(VerificationResult(True, True, False))
        report = await SupplyChainAdmissionService(verifier=verifier, policy=policy()).evaluate(artifact())
        self.assertEqual(report.decision, AdmissionDecision.BLOCK)
        self.assertIn("SIGNATURE_INVALID", {f.code for f in report.findings})

    async def test_untrusted_source_and_publisher_are_quarantined(self):
        item = artifact(
            publisher=PublisherEvidence("external", "partner-idp", True, "identity://external/2"),
            provenance=BuildProvenance("https://evil.example/repo", "b" * 40, "build-9", "ci://other", True),
        )
        report = await SupplyChainAdmissionService(verifier=Verifier(), policy=policy()).evaluate(item)
        self.assertEqual(report.decision, AdmissionDecision.QUARANTINE)
        self.assertIn("SOURCE_REPOSITORY_UNTRUSTED", {f.code for f in report.findings})
        self.assertIn("PUBLISHER_NOT_TRUSTED", {f.code for f in report.findings})

    async def test_vulnerability_malware_license_and_permission_risks(self):
        item = artifact(
            findings=(
                SecurityFinding("CVE-2026-1000", Severity.CRITICAL, "critical dependency vulnerability"),
                SecurityFinding("MALWARE_BEHAVIOR", Severity.HIGH, "unexpected process injection"),
                SecurityFinding("DATA_EXFILTRATION", Severity.HIGH, "outbound secret transfer"),
            ),
            components=(SBOMComponent("runtime", "2.1", "pkg:pypi/runtime@2.1", "internal", "GPL-3.0"),),
            declared_permissions=frozenset({"filesystem.read", "host.root", "network.raw"}),
        )
        report = await SupplyChainAdmissionService(verifier=Verifier(), policy=policy()).evaluate(item)
        self.assertEqual(report.decision, AdmissionDecision.BLOCK)
        codes = {finding.code for finding in report.findings}
        self.assertTrue({"LICENSE_NOT_ALLOWED", "PERMISSION_OVERBROAD", "CVE-2026-1000",
                         "MALWARE_BEHAVIOR", "DATA_EXFILTRATION"} <= codes)

    async def test_sbom_duplicate_and_dependency_confusion_flagged(self):
        component = SBOMComponent("library", "1.0", "pkg:npm/%40acme/library@1.0", "unknown-vendor", "MIT")
        item = artifact(components=(component, component))
        report = await SupplyChainAdmissionService(verifier=Verifier(), policy=policy()).evaluate(item)
        codes = {finding.code for finding in report.findings}
        self.assertIn("SBOM_DUPLICATE_COMPONENT", codes)
        self.assertIn("DEPENDENCY_SUPPLIER_MISMATCH", codes)
        self.assertEqual(report.decision, AdmissionDecision.QUARANTINE)

    async def test_digest_tampering_is_rejected_before_verification(self):
        data = b"tampered"
        with self.assertRaises(ValueError):
            artifact(content=data)

    async def test_tenant_identity_is_part_of_report_and_not_policy_input(self):
        report = await SupplyChainAdmissionService(verifier=Verifier(), policy=policy()).evaluate(
            artifact(tenant_id="tenant-b"))
        self.assertEqual(report.tenant_id, "tenant-b")


if __name__ == "__main__":
    unittest.main()
