import unittest
from contextlib import contextmanager

from agent_platform.approval.policy import RiskLevel
from agent_platform.observability.metrics import InMemoryMetricsRecorder
from agent_platform.tools.gateway import (
    SupervisedToolGateway,
    ToolDefinition,
    ToolExecutionDenied,
)
from agent_platform.tools.idempotency import InMemoryToolEffectStore


class FakeApprovalVerifier:
    def __init__(self, valid=False):
        self.valid = valid
        self.calls = []

    async def consume(self, *, tenant_id, approval_id, action_digest):
        self.calls.append((tenant_id, approval_id, action_digest))
        return self.valid


class FakeAuditSink:
    def __init__(self, fail=False):
        self.records = []
        self.fail = fail

    async def record(self, **kwargs):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.records.append(kwargs)



class FakeSpan:
    def __init__(self):
        self.updates = []
    def update(self, **kwargs):
        self.updates.append(kwargs)


class FakeObserver:
    def __init__(self):
        self.calls = []
        self.spans = []
    @contextmanager
    def observe(self, *, name, input, metadata):
        span = FakeSpan()
        self.calls.append((name, input, metadata))
        self.spans.append(span)
        yield span


class ToolGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.executed = []
        self.approvals = FakeApprovalVerifier()
        self.audit = FakeAuditSink()
        self.effects = InMemoryToolEffectStore()
        self.call_sequence = 0
        self.observer = FakeObserver()
        self.metrics = InMemoryMetricsRecorder()

        async def handler(arguments):
            self.executed.append(dict(arguments))
            return {"ok": True}

        self.gateway = SupervisedToolGateway(
            tools=(
                ToolDefinition("read", RiskLevel.READ_ONLY, handler),
                ToolDefinition(
                    "validated_read", RiskLevel.READ_ONLY, handler,
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                ),
                ToolDefinition("draft", RiskLevel.INTERNAL_REVERSIBLE, handler),
                ToolDefinition("send", RiskLevel.EXTERNAL_OR_IRREVERSIBLE, handler),
                ToolDefinition(
                    "pay", RiskLevel.FINANCIAL_OR_SECURITY, handler
                ),
                ToolDefinition(
                    "admin_only", RiskLevel.READ_ONLY, handler,
                    allowed_roles=("admin",),
                ),
            ),
            approval_verifier=self.approvals,
            audit_sink=self.audit,
            idempotency_store=self.effects,
            observer=self.observer,
            metrics=self.metrics,
        )

    async def call(self, tool, **kwargs):
        self.call_sequence += 1
        defaults = {
            "tenant_id": "tenant-a",
            "actor_id": "agent-a",
            "actor_roles": ("agent",),
            "tool_name": tool,
            "resource": "resource:1",
            "arguments": {"value": "x"},
            "run_id": "00000000-0000-0000-0000-000000000001",
            "call_id": f"call-{self.call_sequence}",
        }
        defaults.update(kwargs)
        return await self.gateway.call(**defaults)

    async def test_read_and_internal_write_are_allowed(self):
        self.assertEqual((await self.call("read"))["ok"], True)
        self.assertEqual((await self.call("draft"))["ok"], True)
        self.assertEqual(len(self.executed), 2)

    async def test_tool_json_schema_is_enforced_before_handler(self):
        with self.assertRaises(ToolExecutionDenied):
            await self.call("validated_read", arguments={"value": 42})
        self.assertEqual(self.executed, [])
        result = await self.call("validated_read", arguments={"value": "ok"})
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.executed), 1)

    async def test_unknown_tool_is_denied(self):
        with self.assertRaises(ToolExecutionDenied):
            await self.call("unknown")
        self.assertEqual(self.executed, [])

    async def test_external_action_requires_consumed_approval(self):
        with self.assertRaises(ToolExecutionDenied):
            await self.call("send")
        self.assertEqual(self.executed, [])

        self.approvals.valid = True
        result = await self.call("send", approval_id="approval-1")
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.approvals.calls), 1)

    async def test_external_action_replay_returns_cached_result_without_reexecution(self):
        self.approvals.valid = True
        kwargs = {
            "approval_id": "approval-replay",
            "call_id": "stable-call-id",
        }
        first = await self.call("send", **kwargs)
        second = await self.call("send", **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(len(self.executed), 1)

    async def test_idempotency_key_cannot_be_reused_for_different_action(self):
        self.approvals.valid = True
        await self.call("send", approval_id="approval-1", call_id="stable-call")
        with self.assertRaises(ToolExecutionDenied):
            await self.call(
                "send", approval_id="approval-2", call_id="stable-call",
                arguments={"value": "different"},
            )
        self.assertEqual(len(self.executed), 1)

    async def test_l3_action_cannot_be_executed_by_agent(self):
        self.approvals.valid = True
        with self.assertRaises(ToolExecutionDenied):
            await self.call("pay", approval_id="approval-2")
        self.assertEqual(self.executed, [])
        self.assertEqual(self.approvals.calls, [])

    async def test_role_restriction_is_enforced(self):
        with self.assertRaises(ToolExecutionDenied):
            await self.call("admin_only")
        result = await self.call("admin_only", actor_roles=("admin",))
        self.assertTrue(result["ok"])

    async def test_audit_failure_blocks_execution(self):
        self.audit.fail = True
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await self.call("read")
        self.assertEqual(self.executed, [])


    async def test_tool_call_emits_child_trace_without_argument_capture(self):
        result = await self.call("read", arguments={"secret_value": "must-not-enter-trace"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.observer.calls[0][0], "tool.call")
        self.assertEqual(self.observer.calls[0][1]["tool_name"], "read")
        self.assertNotIn("must-not-enter-trace", str(self.observer.calls[0][1]))
        self.assertEqual(self.observer.calls[0][2]["run_id"], "00000000-0000-0000-0000-000000000001")
        counters = self.metrics.snapshot()["counters"]
        self.assertIn('tool_calls_total{component="tool",operation="read",status="succeeded"}', counters)
        self.assertIn("tool_call_duration_seconds", " ".join(self.metrics.snapshot()["observation_counts"]))


if __name__ == "__main__":
    unittest.main()


    async def test_correlation_id_is_propagated_into_tool_observation(self):
        await self.call("read", correlation_id="corr-123")
        self.assertEqual(self.observer.calls[-1][2]["correlation_id"], "corr-123")

