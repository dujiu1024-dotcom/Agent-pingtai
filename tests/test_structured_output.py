from __future__ import annotations

import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult
from agent_platform.runtime.structured_output import (
    SchemaValidatedHarnessAdapter,
    StructuredOutputSchemaRegistry,
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
        self.request = HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a", run_id="run-1", agent_id="agent-a",
                model_template=ModelTemplateRef("general", 1),
            ),
            instructions="Return a decision.",
            input_text="Evaluate the evidence.",
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
        adapter = FakeAdapter([
            "not json",
            '{"decision":"approve","score":0.8,"evidence":["source:1"]}',
        ])
        validated = SchemaValidatedHarnessAdapter(adapter, max_retries=1)
        result = await validated.run(self.request)
        self.assertTrue(result.metadata["structured_output_valid"])
        self.assertEqual(result.metadata["structured_output_attempts"], 2)
        self.assertIn("Validation errors", adapter.requests[1].instructions)

    async def test_invalid_output_fails_after_retry_budget(self):
        adapter = FakeAdapter(["{}", "{}"])
        validated = SchemaValidatedHarnessAdapter(adapter, max_retries=1)
        with self.assertRaises(StructuredOutputValidationError) as raised:
            await validated.run(self.request)
        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(len(adapter.requests), 2)

    async def test_versioned_schema_registry_resolves_pinned_version(self):
        registry = StructuredOutputSchemaRegistry()
        schema = {"type": "object", "required": ["answer"], "properties": {
            "answer": {"type": "string"}
        }}
        registry.register("agent.answer", 1, schema)
        schema["properties"]["answer"]["type"] = "integer"
        self.assertEqual(
            registry.resolve("agent.answer", 1)["properties"]["answer"]["type"],
            "string",
        )
        adapter = FakeAdapter(['{"answer":"ok"}'])
        request = HarnessRequest(
            context=self.request.context,
            instructions="Return answer",
            input_text="Question",
            metadata={"output_schema_id": "agent.answer", "output_schema_version": 1},
        )
        result = await SchemaValidatedHarnessAdapter(
            adapter, schema_registry=registry,
        ).run(request)
        self.assertTrue(result.metadata["structured_output_valid"])
        self.assertEqual(result.metadata["output_schema_id"], "agent.answer")
        self.assertEqual(result.metadata["output_schema_version"], 1)
        self.assertEqual(adapter.requests[0].output_schema["type"], "object")

    async def test_schema_versions_are_immutable_and_unknown_versions_fail_closed(self):
        registry = StructuredOutputSchemaRegistry()
        registry.register("agent.answer", 1, {"type": "string"})
        with self.assertRaisesRegex(ValueError, "immutable"):
            registry.register("agent.answer", 1, {"type": "integer"})
        with self.assertRaisesRegex(KeyError, "unknown structured output schema"):
            registry.resolve("agent.answer", 2)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            registry.resolve("agent.answer", True)

    async def test_registry_rejects_schema_keywords_it_cannot_validate(self):
        registry = StructuredOutputSchemaRegistry()
        with self.assertRaisesRegex(ValueError, "unsupported schema keywords"):
            registry.register("unsafe.schema", 1, {"type": "object", "$ref": "#/definitions/secret"})
        with self.assertRaisesRegex(ValueError, "properties must be an object"):
            registry.register("malformed.schema", 1, {"type": "object", "properties": []})

    def test_registry_rejects_unimplemented_or_malformed_keyword_values(self):
        registry = StructuredOutputSchemaRegistry()
        invalid_schemas = [
            {"type": ["string", "null"]},
            {"type": "object", "additionalProperties": {"type": "string"}},
            {"type": "object", "required": ["answer", "answer"]},
            {"type": "number", "minimum": float("inf")},
        ]
        for index, schema in enumerate(invalid_schemas):
            with self.subTest(schema=schema):
                with self.assertRaises(ValueError):
                    registry.register(f"invalid.schema.{index}", 1, schema)

    def test_non_standard_json_numeric_constants_are_rejected(self):
        for raw in ('{"score": NaN}', '{"score": Infinity}', '{"score": -Infinity}'):
            with self.subTest(raw=raw):
                value, errors = validate_structured_output(raw, {"type": "object"})
                self.assertIsNone(value)
                self.assertTrue(errors)
                self.assertIn("invalid JSON", errors[0])

    async def test_versioned_schema_requires_registry_and_explicit_version(self):
        adapter = FakeAdapter(['{"answer":"ok"}'])
        request = HarnessRequest(
            context=self.request.context,
            instructions="Return answer",
            input_text="Question",
            metadata={"output_schema_id": "agent.answer"},
        )
        with self.assertRaisesRegex(ValueError, "version"):
            await SchemaValidatedHarnessAdapter(adapter).run(request)
        request = HarnessRequest(
            context=self.request.context,
            instructions="Return answer",
            input_text="Question",
            metadata={"output_schema_id": "agent.answer", "output_schema_version": 1},
        )
        with self.assertRaisesRegex(RuntimeError, "without a schema registry"):
            await SchemaValidatedHarnessAdapter(adapter).run(request)

    async def test_retry_budget_is_bounded(self):
        for retries in (4, True, -1):
            with self.subTest(retries=retries), self.assertRaises(ValueError):
                SchemaValidatedHarnessAdapter(FakeAdapter([]), max_retries=retries)

    def test_overflowing_json_number_is_rejected(self):
        value, errors = validate_structured_output('{"value":1e999}', {"type": "object"})
        self.assertIsNone(value)
        self.assertTrue(any("invalid JSON" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
