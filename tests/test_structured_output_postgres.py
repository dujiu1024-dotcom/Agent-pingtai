from __future__ import annotations

import json
import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult
from agent_platform.runtime.structured_output import (
    PostgresStructuredOutputSchemaRegistry,
    SchemaValidatedHarnessAdapter,
)


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _connection_factory(rows):
    async def connect():
        return _Connection(rows)
    return connect


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return _Transaction()

    async def execute(self, query, params):
        if query.lstrip().startswith("INSERT INTO platform_output_schemas"):
            tenant_id, schema_id, version, payload, digest = params
            self.rows.setdefault(
                (tenant_id, schema_id, version),
                {"schema_json": json.loads(payload), "schema_digest": digest},
            )
            return _Cursor(None)
        tenant_id, schema_id, version = params
        return _Cursor(self.rows.get((tenant_id, schema_id, version)))


class _Harness:
    def __init__(self):
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return HarnessResult(output_text='{"answer":"ok"}')


class PostgresStructuredOutputSchemaRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_idempotent_registration_and_immutable_versions_across_instances(self):
        rows = {}
        first = PostgresStructuredOutputSchemaRegistry(_connection_factory(rows))
        second = PostgresStructuredOutputSchemaRegistry(_connection_factory(rows))
        schema = {"type": "object", "required": ["answer"],
                  "properties": {"answer": {"type": "string"}}}
        await first.register(
            tenant_id="tenant-a", schema_id="agent.answer", version=1, schema=schema,
        )
        await second.register(
            tenant_id="tenant-a", schema_id="agent.answer", version=1, schema=schema,
        )
        schema["properties"]["answer"]["type"] = "integer"
        resolved = await second.resolve(
            tenant_id="tenant-a", schema_id="agent.answer", version=1,
        )
        self.assertEqual(resolved["properties"]["answer"]["type"], "string")
        with self.assertRaisesRegex(ValueError, "immutable"):
            await second.register(
                tenant_id="tenant-a", schema_id="agent.answer", version=1,
                schema={"type": "integer"},
            )

    async def test_harness_adapter_resolves_persistent_schema_using_run_tenant(self):
        rows = {}
        registry = PostgresStructuredOutputSchemaRegistry(_connection_factory(rows))
        await registry.register(
            tenant_id="tenant-a", schema_id="answer", version=3,
            schema={"type": "object", "required": ["answer"],
                    "properties": {"answer": {"type": "string"}}},
        )
        harness = _Harness()
        request = HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a", run_id="run-1", agent_id="agent-a",
                model_template=ModelTemplateRef("general", 1),
            ),
            instructions="Return an answer",
            input_text="Question",
            metadata={"output_schema_id": "answer", "output_schema_version": 3},
        )
        result = await SchemaValidatedHarnessAdapter(
            harness, schema_registry=registry,
        ).run(request)
        self.assertTrue(result.metadata["structured_output_valid"])
        self.assertEqual(result.metadata["output_schema_version"], 3)
        self.assertEqual(harness.requests[0].output_schema["required"], ["answer"])

    async def test_schema_versions_are_tenant_isolated_and_exactly_pinned(self):
        rows = {}
        registry = PostgresStructuredOutputSchemaRegistry(_connection_factory(rows))
        await registry.register(
            tenant_id="tenant-a", schema_id="answer", version=1, schema={"type": "string"},
        )
        with self.assertRaisesRegex(KeyError, "unknown structured output schema"):
            await registry.resolve(tenant_id="tenant-b", schema_id="answer", version=1)
        with self.assertRaisesRegex(KeyError, "unknown structured output schema"):
            await registry.resolve(tenant_id="tenant-a", schema_id="answer", version=2)


if __name__ == "__main__":
    unittest.main()
