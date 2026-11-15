from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.runtime.protocols import HarnessRequest, HarnessResult
from agent_platform.runtime.structured_output import (
    SchemaValidatedHarnessAdapter,
    StructuredOutputValidationError,
    validate_structured_output,
)


class FakeAdapter:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return HarnessResult(output_text=self.outputs.pop(0))


class StructuredOutputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = SimpleNamespace(
            output_schema={
                "type": "object",
                "required": ["decision", "score"],
                "additionalProperties": False,
                "properties": {
                    "decision": {"type": "string", "enum": ["approve", "reject"]},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
            instructions="Return a decision.",
        )

    def test_schema_validation_reports_nested_paths(self):
        value, errors = validate_structured_output(
            '{"decision":"maybe","score":2,"extra":true}',
            self.request.output_schema,
        )
        self.assertEqual(value["decision"], "maybe")
        self.assertTrue(any("$.decision" in error for error in errors))
        self.assertTrue(any("$.score" in error for error in errors))
        self.assertTrue(any("$.extra" in error for error in errors))

    async def test_invalid_output_is_repaired_once_and_validated(self):
        from dataclasses import replace

        request = replace(
            HarnessRequest.__new__(HarnessRequest),
            output_schema=self.request.output_schema,
        ) if False else self.request
        adapter = FakeAdapter([
            "not json",
            '{"decision":"approve","score":0.8,"evidence":["source:1"]}',
        ])
        validated = SchemaValidatedHarnessAdapter(adapter, max_retries=1)
        result = await validated.run(request)
        self.assertTrue(result.metadata["structured_output_valid"])
        self.assertEqual(result.metadata["structured_output_attempts"], 2)
        self.assertIn("Validation errors", adapter.requests[1].instructions)

    async def test_invalid_output_fails_after_retry_budget(self):
        adapter = FakeAdapter(["{}", "{}"])
        validated = SchemaValidatedHarnessAdapter(adapter, max_retries=1)
        with self.assertRaises(StructuredOutputValidationError):
            await validated.run(self.request)

    async def test_retry_budget_is_bounded(self):
        with self.assertRaises(ValueError):
            SchemaValidatedHarnessAdapter(FakeAdapter([]), max_retries=4)


if __name__ == "__main__":
    unittest.main()
