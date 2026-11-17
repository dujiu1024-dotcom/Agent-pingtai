import unittest
from contextlib import contextmanager

from agent_platform.approval.policy import RiskLevel
from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.observability.metrics import InMemoryMetricsRecorder
from agent_platform.observability.trace_context import TraceContext, TraceContextScope
from agent_platform.observability.trace_spans import current_trace_span, trace_span
from agent_platform.runtime.tool_gateway import (
    InMemoryToolAuditSink,
    InMemoryToolRunRegistry,
    ToolDefinition as RuntimeToolDefinition,
    ToolGateway as RuntimeToolGateway,
    ToolPrincipal,
    ToolRunBinding,
)
from agent_platform.tools.gateway import (
    SupervisedToolGateway,
    ToolDefinition,
    ToolExecutionDenied,
)
from agent_platform.tools.idempotency import (
    InMemoryToolEffectStore,
    ToolEffectReconciliationService,
)

class FakeApprovalVerifier:
    def __init__(self, valid=False):
        self.valid = valid
        self.expected_digest = None
        self.expected_run_id = None
        self.calls = []

    async def consume(
        self, *, tenant_id, approval_id, action_digest, policy_version="1",
        run_id=None,
    ):
        self.calls.append((tenant_id, approval_id, action_digest, policy_version, run_id))
        return (
            self.valid
            and (self.expected_digest is None or action_digest == self.expected_digest)
            and (self.expected_run_id is None or run_id == self.expected_run_id)
        )


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

        def resolve_resource(arguments):
            return f"resource:{arguments.get('value', 'global')}"

        async def authorize_resource(_context, _arguments):
            return True

        self.gateway = SupervisedToolGateway(
            tools=(
                ToolDefinition(
                    "read", RiskLevel.READ_ONLY, handler,
                    resource_resolver=resolve_resource,
                    resource_authorizer=authorize_resource,
                ),
                ToolDefinition(
                    "validated_read", RiskLevel.READ_ONLY, handler,
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    resource_resolver=resolve_resource,
                    resource_authorizer=authorize_resource,
                ),
                ToolDefinition(
                    "draft", RiskLevel.INTERNAL_REVERSIBLE, handler,
                    resource_resolver=resolve_resource,
                    resource_authorizer=authorize_resource,
                ),
                ToolDefinition(
                    "send", RiskLevel.EXTERNAL_OR_IRREVERSIBLE, handler,
                    policy_version="policy-v2",
                    resource_resolver=resolve_resource,
                    resource_authorizer=authorize_resource,
                ),
                ToolDefinition(
                    "pay", RiskLevel.FINANCIAL_OR_SECURITY, handler,
                    resource_resolver=resolve_resource,
                    resource_authorizer=authorize_resource,
                ),
                ToolDefinition(
                    "admin_only", RiskLevel.READ_ONLY, handler,
                    allowed_roles=("admin",),
                    resource_resolver=resolve_resource,
                    resource_authorizer=authorize_resource,
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
            "arguments": {"value": "x"},
            "run_id": "00000000-0000-0000-0000-000000000001",
            "call_id": f"call-{self.call_sequence}",
        }
        defaults.update(kwargs)
        defaults.setdefault(
            "resource", f"resource:{defaults['arguments'].get('value', 'global')}",
        )
        return await self.gateway.call(**defaults)

    async def test_call_resolved_uses_trusted_resource_resolver(self):
        result = await self.gateway.call_resolved(
            tenant_id="tenant-a", actor_id="agent-a", actor_roles=("agent",),
            tool_name="read", arguments={"value": "resolved"}, run_id="run-resolved",
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.executed, [{"value": "resolved"}])
        self.assertEqual(self.audit.records[-1]["resource"], "resource:resolved")

    async def test_correlation_id_is_propagated_into_tool_observation(self):
        await self.call("read", correlation_id="corr-123")
        self.assertEqual(self.observer.calls[-1][2]["correlation_id"], "corr-123")

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


    async def test_gateway_rejects_tool_without_resource_acl_at_composition(self):
        async def handler(_arguments):
            return {"ok": True}

        with self.assertRaisesRegex(ValueError, "resource_resolver and resource_authorizer"):
            SupervisedToolGateway(
                tools=(ToolDefinition("unscoped", RiskLevel.READ_ONLY, handler),),
                approval_verifier=self.approvals,
                audit_sink=self.audit,
            )

    async def test_gateway_rejects_caller_supplied_resource_mismatch(self):
        with self.assertRaisesRegex(ToolExecutionDenied, "does not match trusted"):
            await self.call("read", resource="resource:another-tenant")
        self.assertEqual(self.executed, [])
        self.assertEqual(self.audit.records[-1]["outcome"], "denied")
        self.assertEqual(self.audit.records[-1]["detail"], "resource_resolution_mismatch")
        self.assertEqual(len(self.audit.records[-1]["action_digest"]), 64)

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
        self.assertEqual(self.approvals.calls[0][3], "policy-v2")

    async def test_external_action_approval_is_bound_to_exact_parameters_and_run(self):
        from agent_platform.approval.policy import ActionRequest

        self.approvals.valid = True
        self.approvals.expected_run_id = "approved-run"
        self.approvals.expected_digest = ActionRequest(
            action="send", resource="resource:x",
            parameters={"value": "x"}, actor_id="agent-a",
        ).digest()

        with self.assertRaises(ToolExecutionDenied):
            await self.call(
                "send", approval_id="approval-bound",
                run_id="approved-run", arguments={"value": "changed"},
            )
        with self.assertRaises(ToolExecutionDenied):
            await self.call(
                "send", approval_id="approval-bound",
                run_id="different-run",
            )
        self.assertEqual(self.executed, [])

        result = await self.call(
            "send", approval_id="approval-bound",
            run_id="approved-run",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(self.approvals.calls[-1][4], "approved-run")

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
        self.assertEqual(self.observer.calls[0][2]["trace_component"], "tool")
        self.assertTrue(self.observer.calls[0][2]["span_id"])
        counters = self.metrics.snapshot()["counters"]
        self.assertIn('tool_calls_total{component="tool",operation="read",status="succeeded"}', counters)
        self.assertIn("tool_call_duration_seconds", " ".join(self.metrics.snapshot()["observation_counts"]))


    async def test_manual_reconciliation_allows_retry_only_after_confirming_no_effect(self):
        run_id = "00000000-0000-0000-0000-000000000001"
        claimed = await self.effects.claim(
            tenant_id="tenant-a", run_id=run_id, call_id="call-reconcile",
            action_digest="a" * 64, actor_id="agent-a",
        )
        self.assertEqual(claimed.state, "pending")
        await self.effects.fail(
            tenant_id="tenant-a", run_id=run_id, call_id="call-reconcile",
            error="provider_timeout",
        )
        service = ToolEffectReconciliationService(
            store=self.effects,
            authorize_operator=lambda operator, tenant, run, call: operator == "operator-a",
            audit_sink=self.audit,
        )
        with self.assertRaises(PermissionError):
            await service.reconcile(
                tenant_id="tenant-a", run_id=run_id, call_id="call-reconcile",
                operator_id="agent-a", resolution="confirmed_not_applied",
                note="not authorized",
            )
        effect = await service.reconcile(
            tenant_id="tenant-a", run_id=run_id, call_id="call-reconcile",
            operator_id="operator-a", resolution="confirmed_not_applied",
            note="Provider request ID checked; no side effect was created.",
        )
        self.assertEqual(effect.state, "retryable")
        retried = await self.effects.claim(
            tenant_id="tenant-a", run_id=run_id, call_id="call-reconcile",
            action_digest="a" * 64, actor_id="agent-a",
        )
        self.assertTrue(retried.created)
        self.assertEqual(retried.state, "pending")
        self.assertEqual(effect.reconciled_by, "operator-a")
        self.assertEqual(
            [record["outcome"] for record in self.audit.records[-2:]],
            ["reconciliation_started", "reconciliation_completed"],
        )

    async def test_manual_reconciliation_can_confirm_success_or_compensation(self):
        run_id = "00000000-0000-0000-0000-000000000001"
        service = ToolEffectReconciliationService(
            store=self.effects, authorize_operator=lambda *_: True, audit_sink=self.audit,
        )
        for call_id, resolution, result, expected in (
            ("call-confirmed", "confirmed_succeeded", {"receipt": "r-1"}, "succeeded"),
            ("call-compensated", "compensated", None, "compensated"),
        ):
            await self.effects.claim(
                tenant_id="tenant-a", run_id=run_id, call_id=call_id,
                action_digest=(call_id[0] * 64), actor_id="agent-a",
            )
            await self.effects.fail(
                tenant_id="tenant-a", run_id=run_id, call_id=call_id,
                error="ambiguous_provider_result",
            )
            effect = await service.reconcile(
                tenant_id="tenant-a", run_id=run_id, call_id=call_id,
                operator_id="operator-a", resolution=resolution,
                note="Checked external provider and recorded resolution.",
                result=result,
            )
            self.assertEqual(effect.state, expected)
        confirmed = await self.effects.get(
            tenant_id="tenant-a", run_id=run_id, call_id="call-confirmed",
        )
        self.assertEqual(confirmed.result, {"receipt": "r-1"})



class RuntimeToolGatewayTraceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.exported = []
        self.observed = []
        self.raise_error = False
        self.registry = InMemoryToolRunRegistry()
        self.registry.bind(ToolRunBinding(
            tenant_id="tenant-16",
            run_id="run-16",
            agent_id="agent-16",
            project_id="project-16",
            allowed_tools=frozenset({"search"}),
        ))

        class Authenticator:
            async def authenticate(_self, credential):
                if credential != "trusted-token":
                    return None
                return ToolPrincipal(
                    subject_id="actor-16",
                    tenant_id="tenant-16",
                    agent_id="agent-16",
                    scopes=frozenset({"tools.invoke"}),
                )

        async def handler(_arguments, context):
            parent = current_trace_span()
            self.observed.append(parent)
            if self.raise_error:
                raise RuntimeError("provider failed")
            with trace_span(
                "tool.handler.child",
                component="custom",
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                agent_id=context.agent_id,
                correlation_id=context.correlation_id,
                exporter=self.exported.append,
            ) as child:
                self.observed.append(child)
            return {"ok": True}

        self.gateway = RuntimeToolGateway(
            authenticator=Authenticator(),
            run_registry=self.registry,
            audit_sink=InMemoryToolAuditSink(),
            trace_exporter=self.exported.append,
        )
        self.gateway.register(RuntimeToolDefinition("search", handler))

        self.run_context = RunContext(
            tenant_id="tenant-16",
            run_id="run-16",
            agent_id="agent-16",
            model_template=ModelTemplateRef("support"),
            project_id="project-16",
            correlation_id="corr-16",
        )

    async def test_invocation_creates_child_span_and_propagates_context_to_handler(self):
        request = TraceContext(
            "1234567890abcdef1234567890abcdef",
            "1234567890abcdef",
            correlation_id="corr-16",
        )
        with TraceContextScope(request):
            with trace_span(
                "run.execute",
                component="run",
                tenant_id="tenant-16",
                run_id="run-16",
                agent_id="agent-16",
                correlation_id="corr-16",
            ) as run_span:
                result = await self.gateway.invoke(
                    credential="trusted-token",
                    run_context=self.run_context,
                    tool_name="search",
                    arguments={"query": "safe"},
                    call_id="call-16",
                )

        self.assertEqual(result, {"ok": True})
        tool_span, child_span = self.observed
        self.assertEqual(tool_span.component, "tool")
        self.assertEqual(tool_span.trace_id, request.trace_id)
        self.assertEqual(tool_span.parent_span_id, run_span.span_id)
        self.assertEqual(tool_span.run_id, "run-16")
        self.assertEqual(tool_span.agent_id, "agent-16")
        self.assertEqual(tool_span.correlation_id, "corr-16")
        self.assertEqual(child_span.trace_id, tool_span.trace_id)
        self.assertEqual(child_span.parent_span_id, tool_span.span_id)
        self.assertEqual([item["component"] for item in self.exported], ["custom", "tool"])
        self.assertTrue(all(item["status"] == "ok" for item in self.exported))

    async def test_failed_tool_exports_error_span_without_recording_arguments(self):
        self.raise_error = True
        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            await self.gateway.invoke(
                credential="trusted-token",
                run_context=self.run_context,
                tool_name="search",
                arguments={"api_key": "never-export-this"},
                call_id="call-error",
            )

        tool_span = self.exported[-1]
        self.assertEqual(tool_span["component"], "tool")
        self.assertEqual(tool_span["status"], "error")
        self.assertEqual(tool_span["attributes"]["error_type"], "RuntimeError")
        self.assertNotIn("never-export-this", str(tool_span))
        self.assertNotIn("api_key", str(tool_span))

    async def test_trace_exporter_failure_does_not_change_tool_result(self):
        async def handler(_arguments, _context):
            return "ok"

        def broken_exporter(_span):
            raise RuntimeError("telemetry unavailable")

        gateway = RuntimeToolGateway(
            authenticator=self.gateway.authenticator,
            run_registry=self.registry,
            audit_sink=InMemoryToolAuditSink(),
            trace_exporter=broken_exporter,
        )
        gateway.register(RuntimeToolDefinition("search", handler))
        result = await gateway.invoke(
            credential="trusted-token",
            run_context=self.run_context,
            tool_name="search",
            arguments={},
            call_id="call-exporter-failure",
        )
        self.assertEqual(result, "ok")


if __name__ == "__main__":
    unittest.main()

