"""Tenant and permission boundary tests for Workflow compiler endpoints."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.workflow_compiler import create_workflow_compiler_router
from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.identity.models import Principal
from agent_platform.workflows.definition_compiler import (
    LangGraphWorkflowCompiler,
    WorkflowNodeRegistry,
    WorkflowNodeType,
)


class WorkflowCompilerApiTests(unittest.TestCase):
    def setUp(self):
        self.principal = Principal(
            user_id="actor-a", tenant_id="tenant-a", session_id="session-a",
            email="actor@example.com", display_name="Actor",
        )
        self.repository = InMemoryRegistryRepository()
        self.registry = RegistryService(self.repository)
        spec = WorkflowNodeType(
            node_type="agent", version=1, factory=lambda config: lambda state: state,
            config_schema={"type": "object"}, input_schema={"type": "object"},
            output_schema={"type": "object"},
            ports={"inputs": ["default"], "outputs": ["default"]},
        )
        self.compiler = LangGraphWorkflowCompiler(
            node_registry=WorkflowNodeRegistry([spec]),
            graph_factory=lambda definition, nodes: object(),
        )

        async def resolve_principal(request):
            return self.principal

        async def resolve_registry(principal):
            return self.registry

        async def resolve_compiler(principal):
            return self.compiler

        async def authorize(principal, kind, item_id, action):
            return principal.tenant_id == "tenant-a" and action == "compile"

        app = FastAPI()
        app.include_router(create_workflow_compiler_router(
            resolve_principal=resolve_principal,
            resolve_registry=resolve_registry,
            resolve_compiler=resolve_compiler,
            authorize=authorize,
        ))
        self.client = TestClient(app)
        self.definition = {
            "schema_version": 1, "entry_point": "start",
            "nodes": [{
                "id": "start", "node_type": "agent", "version": 1, "config": {},
                "config_schema": {"type": "object"}, "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "ports": {"inputs": ["default"], "outputs": ["default"]},
                "permissions": [], "risk": "low", "error_retry": {},
            }],
            "edges": [{"source": "start", "target": "__end__"}], "branches": [],
        }

    def test_diagnostics_reads_the_requested_immutable_registry_version(self):
        item = self._create_workflow()
        response = self.client.get(f"/api/platform/v1/workflows/flow-a/versions/{item.version}/diagnostics")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["valid"])
        self.assertEqual(response.json()["version"], item.version)

    def test_compile_is_pinned_to_registry_version_and_does_not_execute(self):
        item = self._create_workflow()
        response = self.client.post(f"/api/platform/v1/workflows/flow-a/versions/{item.version}/compile")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["definition_status"], "draft")
        self.assertFalse(response.json()["execution_started"])
        self.assertEqual(response.json()["engine"], "langgraph")

    def test_unknown_node_returns_path_addressed_422(self):
        self.definition["nodes"][0]["node_type"] = "unregistered"
        item = self._create_workflow()
        response = self.client.post(f"/api/platform/v1/workflows/flow-a/versions/{item.version}/compile")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["diagnostics"][0]["code"], "node.unknown")

    def test_missing_version_returns_404(self):
        response = self.client.get("/api/platform/v1/workflows/flow-a/versions/999/diagnostics")
        self.assertEqual(response.status_code, 404)

    def test_compile_permission_is_required(self):
        async def deny(principal, kind, item_id, action):
            return False

        app = FastAPI()
        app.include_router(create_workflow_compiler_router(
            resolve_principal=lambda request: self._principal_async(),
            resolve_registry=lambda principal: self._registry_async(),
            resolve_compiler=lambda principal: self._compiler_async(),
            authorize=deny,
        ))
        response = TestClient(app).get("/api/platform/v1/workflows/flow-a/versions/1/diagnostics")
        self.assertEqual(response.status_code, 403)

    def _create_workflow(self):
        import asyncio
        return asyncio.run(self.registry.create_draft(
            tenant_id="tenant-a", kind="workflow", item_id="flow-a",
            definition=self.definition, actor_id="actor-a",
        ))

    async def _principal_async(self):
        return self.principal

    async def _registry_async(self):
        return self.registry

    async def _compiler_async(self):
        return self.compiler


if __name__ == "__main__":
    unittest.main()
