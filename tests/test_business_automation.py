from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.business_automation import (
    ConnectorCapability, InMemoryWorkflowDefinitionRepository, WorkflowDefinitionError,
    WorkflowEdge, WorkflowNode, connector_capability_catalog,
    create_business_automation_router, validate_workflow_graph,
)
from agent_platform.identity.models import Principal


def sample_graph():
    nodes = (
        WorkflowNode("start", "trigger", {"trigger_type": "event", "event": "purchase.order.created"}),
        WorkflowNode("check", "condition", {"expression": "amount > 1000"}),
        WorkflowNode("route", "branch", {"strategy": "exclusive"}),
        WorkflowNode("erp", "connector_action", {"connector_id": "erp-main", "operation": "create"}),
        WorkflowNode("oa", "approval", {"policy": "purchase-approval"}),
        WorkflowNode("done", "end", {}),
    )
    edges = (
        WorkflowEdge("start", "check"),
        WorkflowEdge("check", "route"),
        WorkflowEdge("route", "erp", label="low-risk"),
        WorkflowEdge("route", "oa", label="high-risk"),
        WorkflowEdge("erp", "done"),
        WorkflowEdge("oa", "done"),
    )
    return nodes, edges


class BusinessAutomationModelTests(unittest.TestCase):
    def test_validates_trigger_condition_branch_and_approval_graph(self):
        nodes, edges = sample_graph()
        definition = validate_workflow_graph(
            workflow_id="purchase-flow", tenant_id="tenant-a", name="Purchase",
            version=1, trigger_type="event", nodes=nodes, edges=edges, created_by="user-a",
        )
        self.assertEqual(definition.nodes[0].node_type, "trigger")
        self.assertEqual(len(definition.edges), 6)

    def test_rejects_cycles_and_unreachable_nodes(self):
        nodes, edges = sample_graph()
        cyclic = edges + (WorkflowEdge("done", "check"),)
        with self.assertRaisesRegex(WorkflowDefinitionError, "acyclic"):
            validate_workflow_graph(
                workflow_id="flow", tenant_id="t", name="Flow", version=1,
                trigger_type="event", nodes=nodes, edges=cyclic, created_by="u",
            )
        disconnected = nodes + (WorkflowNode("orphan", "condition", {}),)
        with self.assertRaisesRegex(WorkflowDefinitionError, "connected"):
            validate_workflow_graph(
                workflow_id="flow", tenant_id="t", name="Flow", version=1,
                trigger_type="event", nodes=disconnected, edges=edges, created_by="u",
            )

    def test_branch_requires_explicit_outcome_and_parallel_requires_fanout(self):
        nodes, edges = sample_graph()
        bad_edges = tuple(
            WorkflowEdge(e.source, e.target, condition=e.condition)
            if e.source == "route" else e for e in edges
        )
        with self.assertRaisesRegex(WorkflowDefinitionError, "branch edges"):
            validate_workflow_graph(
                workflow_id="flow", tenant_id="t", name="Flow", version=1,
                trigger_type="event", nodes=nodes, edges=bad_edges, created_by="u",
            )
        parallel_nodes = tuple(
            WorkflowNode(n.node_id, "parallel", n.config) if n.node_id == "route" else n
            for n in nodes
        )
        validate_workflow_graph(
            workflow_id="flow", tenant_id="t", name="Flow", version=1,
            trigger_type="event", nodes=parallel_nodes, edges=edges, created_by="u",
        )

    def test_subflow_and_connector_action_require_references(self):
        nodes, edges = sample_graph()
        bad = tuple(WorkflowNode("erp", "subflow", {}) if n.node_id == "erp" else n for n in nodes)
        with self.assertRaisesRegex(WorkflowDefinitionError, "subflow"):
            validate_workflow_graph(
                workflow_id="flow", tenant_id="t", name="Flow", version=1,
                trigger_type="event", nodes=bad, edges=edges, created_by="u",
            )
        bad = tuple(WorkflowNode("erp", "connector_action", {"operation": "create"}) if n.node_id == "erp" else n for n in nodes)
        with self.assertRaisesRegex(WorkflowDefinitionError, "connector_action"):
            validate_workflow_graph(
                workflow_id="flow", tenant_id="t", name="Flow", version=1,
                trigger_type="event", nodes=bad, edges=edges, created_by="u",
            )

    def test_connector_contract_rejects_plaintext_cross_tenant_and_ambiguous_operations(self):
        valid = ConnectorCapability(
            connector_id="erp-1", tenant_id="tenant-a", system_type="erp", provider="acme",
            version="1.2", read_operations=("read",), write_operations=("create",),
            scopes=("records.read", "records.write"),
            credential_ref="secret://tenant-a/credentials/erp", supports_idempotency=True,
            supports_webhooks=True,
        )
        valid.validate()
        with self.assertRaisesRegex(WorkflowDefinitionError, "secret reference"):
            ConnectorCapability(
                **{**valid.__dict__, "credential_ref": "plain-token"}
            ).validate()
        with self.assertRaisesRegex(WorkflowDefinitionError, "both read and write"):
            ConnectorCapability(
                **{**valid.__dict__, "write_operations": ("read",)}
            ).validate()

    def test_catalog_declares_erp_crm_oa_mes_finance_hr_without_claiming_live_provider(self):
        catalog = connector_capability_catalog()
        self.assertEqual({item["system_type"] for item in catalog}, {"erp", "crm", "oa", "mes", "finance", "hr"})
        self.assertTrue(all(item["live_provider_configured"] is False for item in catalog))
        self.assertTrue(all(item["idempotency_required_for"] for item in catalog))


class BusinessAutomationApiTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryWorkflowDefinitionRepository()
        async def principal(request):
            return Principal(
                user_id="user-a", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="u@example.test", display_name="User",
            )
        async def authorize(p, workflow_id, action):
            return action != "workflow.create-denied" and p.tenant_id == "tenant-a"
        app = FastAPI()
        app.include_router(create_business_automation_router(
            resolve_principal=principal, repository=self.repository, authorize=authorize,
        ))
        self.client = TestClient(app)

    def test_create_and_read_definition_is_tenant_scoped(self):
        nodes, edges = sample_graph()
        payload = {
            "workflow_id": "purchase-flow", "name": "Purchase", "version": 1,
            "trigger_type": "event",
            "nodes": [{"node_id": n.node_id, "node_type": n.node_type, "config": dict(n.config)} for n in nodes],
            "edges": [{"source": e.source, "target": e.target, "label": e.label, "condition": e.condition} for e in edges],
        }
        created = self.client.post("/api/platform/v1/business-automation/definitions", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        read = self.client.get("/api/platform/v1/business-automation/definitions/purchase-flow/versions/1")
        self.assertEqual(read.status_code, 200)
        other_tenant = self.client.get(
            "/api/platform/v1/business-automation/definitions/purchase-flow/versions/1",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(other_tenant.status_code, 403)

    def test_invalid_graph_is_rejected_and_versions_are_immutable(self):
        nodes, edges = sample_graph()
        payload = {
            "workflow_id": "purchase-flow", "name": "Purchase", "version": 1,
            "trigger_type": "event",
            "nodes": [{"node_id": n.node_id, "node_type": n.node_type, "config": dict(n.config)} for n in nodes],
            "edges": [{"source": e.source, "target": e.target, "label": e.label} for e in edges],
        }
        self.assertEqual(self.client.post("/api/platform/v1/business-automation/definitions", json=payload).status_code, 201)
        duplicate = self.client.post("/api/platform/v1/business-automation/definitions", json=payload)
        self.assertEqual(duplicate.status_code, 422)
        payload["version"] = 2
        payload["edges"][2].pop("label")
        invalid = self.client.post("/api/platform/v1/business-automation/definitions", json=payload)
        self.assertEqual(invalid.status_code, 422)

    def test_connector_capability_catalog_requires_authenticated_tenant(self):
        response = self.client.get("/api/platform/v1/business-automation/connector-capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 6)


if __name__ == "__main__":
    unittest.main()
