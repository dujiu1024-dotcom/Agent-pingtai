from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.runtime.adapter_contract import (
    RuntimeAdapterCompatibilityError,
    RuntimeAdapterKind,
    RuntimeAdapterManifest,
    RuntimeCapabilities,
    RuntimeExecutionContext,
    RuntimeExecutionPhase,
    RuntimeExecutionResult,
    UnsupportedRuntimeCapability,
    validate_adapter_compatibility,
)


def manifest(**overrides):
    values = {
        "adapter_id": "openai-chat",
        "kind": RuntimeAdapterKind.MODEL,
        "adapter_version": "2.1.0",
        "min_contract_version": "1.0.0",
        "max_contract_version": "1.4.0",
        "capabilities": RuntimeCapabilities(
            modalities=frozenset({"text", "image"}),
            streaming=True,
            cancellation=True,
            structured_output=True,
        ),
    }
    values.update(overrides)
    return RuntimeAdapterManifest(**values)


class RuntimeAdapterContractTests(unittest.TestCase):
    def test_compatible_host_contract_version_is_accepted(self):
        validate_adapter_compatibility(manifest(), host_contract_version="1.3.0")

    def test_incompatible_contract_version_fails_closed(self):
        with self.assertRaises(RuntimeAdapterCompatibilityError):
            validate_adapter_compatibility(manifest(), host_contract_version="2.0.0")

    def test_manifest_rejects_invalid_version_and_cross_major_range(self):
        with self.assertRaisesRegex(ValueError, "MAJOR.MINOR.PATCH"):
            manifest(adapter_version="v2")
        with self.assertRaisesRegex(ValueError, "within one major"):
            manifest(max_contract_version="2.0.0")

    def test_required_capabilities_are_checked_as_a_subset(self):
        adapter = manifest()
        adapter.require(RuntimeCapabilities(
            modalities=frozenset({"text", "image"}),
            streaming=True,
            cancellation=True,
        ))
        with self.assertRaises(UnsupportedRuntimeCapability):
            adapter.require(RuntimeCapabilities(
                modalities=frozenset({"text", "video"}),
                tool_calls=True,
            ))

    def test_context_requires_tenant_run_actor_and_request_scope(self):
        with self.assertRaisesRegex(ValueError, "tenant_id"):
            RuntimeExecutionContext("", "run", "actor", "request")
        context = RuntimeExecutionContext(
            "tenant", "run", "actor", "request",
            deadline_at=datetime.now(timezone.utc),
        )
        self.assertEqual(context.run_id, "run")

    def test_execution_result_must_be_terminal_and_usage_non_negative(self):
        with self.assertRaisesRegex(ValueError, "terminal"):
            RuntimeExecutionResult(output=None, phase=RuntimeExecutionPhase.RUNNING)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            RuntimeExecutionResult(
                output=None, phase=RuntimeExecutionPhase.SUCCEEDED,
                usage={"input_tokens": -1},
            )


if __name__ == "__main__":
    unittest.main()
