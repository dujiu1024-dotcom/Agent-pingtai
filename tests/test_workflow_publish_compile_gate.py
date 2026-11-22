"""Workflow Registry publication is fail-closed on LangGraph compilation."""
import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.registry import create_registry_router
from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.identity.models import Principal
from agent_platform.workflows.definition_compiler import (
    LangGraphWorkflowCompiler,
    WorkflowNodeRegistry,
    WorkflowNodeType,
)


class WorkflowPublishCompileGateTests(unittest.TestCase):
    def setUp(self):
        self.principal = Principal(
            user_id="actor-a", tenant_id="tenant-a", session_id="session-a",
            email="actor@example.com", display_name="Actor",
        )
        self.service = RegistryService(InMemoryRegistryRepository())
        self.spec = WorkflowNodeType(
            node_type="agent", version=1, factory=lambda config: lambda state: state,
            config_schema={"type": "object"}, input_schema={"type": "object"},
            output_schema={"type": "object"},
            ports={"inputs": ["default"], "outputs": ["default"]},
        )
        self.compiler = LangGraphWorkflowCompiler(
            node_registry=WorkflowNodeRegistry([self.spec]),
            graph_factory=lambda definition, nodes: object(),
        )

        async def principal(request):
            return self.principal

        async def service(principal):
            return self.service

        async def compiler(principal):
            return self.compiler

        async def authorize(principal, kind, item_id, action):
            return action in {"read", "write", "publish"}

        self.principal_resolver = principal
        self.service_resolver = service
        self.compiler_resolver = compiler
        self.authorizer = authorize

    def _client(self, *, with_compiler=True):
        app = FastAPI()
        kwargs = {}
        if with_compiler:
            kwargs["resolve_workflow_compiler"] = self.compiler_resolver
        app.include_router(create_registry_router(
            resolve_principal=self.principal_resolver,
            resolve_service=self.service_resolver,
            authorize=self.authorizer,
            **kwargs,
        ))
        return TestClient(app)

    def _draft(self, node_type="agent"):
        return asyncio.run(self.service.create_draft(
            tenant_id="tenant-a", kind="workflow", item_id="flow-a",
            actor_id="actor-a",
            definition={
                "schema_version": 1, "entry_point": "step",
                "nodes": [{
                    "id": "step", "node_type": node_type, "version": 1, "config": {},
                    "config_schema": {"type": "object"}, "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "ports": {"inputs": ["default"], "outputs": ["default"]},
                    "permissions": [], "risk": "low", "error_retry": {},
                }],
                "edges": [{"source": "step", "target": "__end__"}],
            },
        ))

    def test_workflow_publish_is_disabled_without_compiler(self):
        draft = self._draft()
        response = self._client(with_compiler=False).post(
            f"/api/platform/v1/registry/workflow/flow-a/versions/{draft.version}/publish"
        )
        self.assertEqual(response.status_code, 503)

    def test_invalid_definition_cannot_be_published(self):
        draft = self._draft(node_type="unregistered")
        response = self._client().post(
            f"/api/platform/v1/registry/workflow/flow-a/versions/{draft.version}/publish"
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "workflow_compile_rejected")

    def test_valid_definition_compiles_before_registry_publish(self):
        draft = self._draft()
        response = self._client().post(
            f"/api/platform/v1/registry/workflow/flow-a/versions/{draft.version}/publish"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "published")
        self.assertEqual(response.json()["version"], draft.version + 1)


if __name__ == "__main__":
    unittest.main()
