from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from fastapi import FastAPI

from agent_platform.api.identity import create_identity_router
from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.contracts import BlackboardEntry, EntryKind
from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.identity.http import IdentityASGIMiddleware
from agent_platform.identity.models import AuthorizationContext, IssuedSession, Principal
from agent_platform.identity.service import AuthenticationError
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.service import InMemoryMemoryRepository, MemoryService
from agent_platform.workflows.blackboard_services import BlackboardMultiExpertServices
from agent_platform.workflows.expert_registry import ControlPlaneExpertRegistry
from agent_platform.workflows.multi_expert import build_multi_expert_workflow


class JourneyIdentity:
    async def login(self, *, email, password, tenant_id, session_ttl):
        if (email, password, tenant_id) != ("operator@example.test", "valid", "tenant-e2e"):
            raise AuthenticationError("invalid credentials")
        return IssuedSession(
            session_id="session-e2e", tenant_id=tenant_id, user_id="operator",
            access_token="e2e-token",
            expires_at=datetime.now(timezone.utc) + session_ttl,
        )

    async def authenticate(self, token):
        if token != "e2e-token":
            raise AuthenticationError("invalid credentials")
        return Principal(
            user_id="operator", tenant_id="tenant-e2e", session_id="session-e2e",
            email="operator@example.test", display_name="Operator",
        )

    async def authenticate_api_credential(self, token):
        raise AuthenticationError("invalid credentials")

    async def logout(self, principal):
        return True


class JourneyIdentityRepository:
    async def get_authorization_context(self, *, tenant_id, user_id):
        if (tenant_id, user_id) != ("tenant-e2e", "operator"):
            return None
        return AuthorizationContext(
            tenant_id=tenant_id, user_id=user_id,
            tenant_roles=frozenset({"tenant_admin"}),
            effective_role_ids=frozenset({"tenant_admin"}),
            tenant_permissions=frozenset({"knowledge.manage", "memory.manage"}),
            project_ids=frozenset(), knowledge_manage_project_ids=frozenset(),
            memory_manage_project_ids=frozenset(), agent_ids=frozenset(),
            memory_manage_agent_ids=frozenset(),
        )


class JourneyBlackboard:
    def __init__(self):
        self.tenant_id = "tenant-e2e"
        self.entries: dict[str, BlackboardEntry] = {}

    async def get(self, entry_id):
        return self.entries.get(entry_id)

    async def propose(self, entry):
        if entry.entry_id in self.entries:
            raise RuntimeError("duplicate blackboard entry")
        self.entries[entry.entry_id] = entry
        return entry

    async def list_entries(self, *, scope, scope_id, kinds=(), limit=100):
        return [
            entry for entry in self.entries.values()
            if entry.scope is scope and entry.scope_id == scope_id
            and (not kinds or entry.kind.value in kinds)
        ][:limit]

    async def list_versions(self, entry_id):
        item = self.entries.get(entry_id)
        return [item] if item else []

    async def revise(self, entry_id, *, expected_version, replacement):
        current = self.entries[entry_id]
        if current.version != expected_version:
            raise RuntimeError("version conflict")
        revised = replacement.__class__(
            **{**replacement.__dict__, "version": expected_version + 1}
        )
        self.entries[entry_id] = revised
        return revised

    async def record_challenge(self, *args, **kwargs):
        raise NotImplementedError

    async def record_decision(self, *args, **kwargs):
        raise NotImplementedError


class JourneyCallbacks:
    def __init__(self):
        self.experts: list[str] = []

    async def plan_experts(self, *, objective, run_context):
        return [
            {"expert_id": "researcher", "focus": "evidence"},
            {"expert_id": "reviewer", "focus": "quality"},
        ]

    async def run_expert(self, *, assignment, objective, run_context):
        self.experts.append(assignment["expert_id"])
        return {"expert_id": assignment["expert_id"], "evidence": "retrieved runbook evidence"}

    async def synthesize(self, *, objective, contributions, run_context):
        return {"summary": "reviewed delivery plan", "evidence_count": len(contributions)}

    async def review(self, *, synthesis, contributions, run_context):
        return {"conflicts": [], "requires_approval": True}

    async def resolve_conflicts(self, *, synthesis, conflicts, run_context):
        return {"synthesis": dict(synthesis), "requires_approval": True}

    async def prepare_approval(self, *, state):
        return {"action": "deliver_artifact", "summary": state["synthesis"]["summary"]}

    async def apply_approval(self, *, state, decision):
        return {"approved": decision.get("approved") is True, "approver_id": "operator"}

    async def archive(self, *, state):
        return {"summary": state["synthesis"]["summary"], "expert_count": len(state["contributions"])}

    async def reject(self, *, state):
        return {"reason": "approval rejected"}


class CoreBusinessJourneyE2ETests(unittest.TestCase):
    def test_login_agent_configuration_knowledge_multi_expert_approval_and_delivery(self):
        try:
            from fastapi.testclient import TestClient
            from langgraph.checkpoint.memory import InMemorySaver
            from langgraph.types import Command
        except ImportError:
            self.skipTest("FastAPI test client and LangGraph are installed in platform CI")

        identity = JourneyIdentity()
        knowledge = KnowledgeService(InMemoryKnowledgeRepository())
        memory = MemoryService(InMemoryMemoryRepository())

        class GroundedGenerator:
            async def generate(self, *, query, context):
                return {
                    "answer": "Approval is required before production delivery.",
                    "citation_ids": [context[0]["citation_id"]],
                }

        app = FastAPI()
        app.add_middleware(
            IdentityASGIMiddleware, identity=identity,
            allowed_origins=("https://platform.example.test",),
        )
        app.include_router(create_identity_router(identity=identity))
        app.include_router(create_knowledge_memory_router(
            knowledge=knowledge, memory=memory,
            identity_repository=JourneyIdentityRepository(),
            rag=RAGService(knowledge=knowledge, generator=GroundedGenerator()),
        ))

        with TestClient(app, base_url="https://platform.example.test") as client:
            login = client.post("/auth/login", json={
                "email": "operator@example.test", "password": "valid",
                "tenant_id": "tenant-e2e",
            })
            self.assertEqual(login.status_code, 200)
            csrf = client.cookies.get("__Host-platform_csrf")
            headers = {"X-CSRF-Token": csrf}
            created = client.post(
                "/api/platform/v1/knowledge/documents", headers=headers,
                json={
                    "title": "Delivery runbook", "scope": "tenant",
                    "content": "Approval is required before production delivery.",
                },
            )
            self.assertEqual(created.status_code, 200)
            answer = client.post(
                "/api/platform/v1/knowledge/answer", headers=headers,
                json={"query": "What is required before production delivery?"},
            )
            self.assertEqual(answer.status_code, 200)
            self.assertTrue(answer.json()["citations"])

        # Configure and publish two tenant-scoped Agent definitions.
        registry = RegistryService(InMemoryRegistryRepository())
        for agent_id, capability in (("researcher", "knowledge.search"), ("reviewer", "artifact.review")):
            draft = asyncio.run(registry.create_draft(
                tenant_id="tenant-e2e", kind="agent", item_id=agent_id,
                definition={"name": agent_id.title(), "capabilities": [capability]},
                actor_id="operator",
            ))
            published = asyncio.run(registry.publish(
                tenant_id="tenant-e2e", kind="agent", item_id=agent_id,
                version=draft.version,
            ))
            self.assertEqual(published.status, "published")

        callbacks = JourneyCallbacks()
        blackboard = JourneyBlackboard()
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-e2e", repository=blackboard, callbacks=callbacks,
            expert_registry=ControlPlaneExpertRegistry(
                registry_service=registry, tenant_id="tenant-e2e",
            ),
        )
        graph = build_multi_expert_workflow(services=services, checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "tenant-e2e-run"}}
        paused = asyncio.run(graph.ainvoke({
            "tenant_id": "tenant-e2e", "task_id": "task-e2e", "run_id": "run-e2e",
            "objective": "Prepare the approved delivery artifact",
            "run_context": {"knowledge_answer": answer.json()["answer"]},
        }, config=config))
        self.assertIn("__interrupt__", paused)
        self.assertEqual(set(callbacks.experts), {"researcher", "reviewer"})
        completed = asyncio.run(graph.ainvoke(
            Command(resume={"approved": True}), config=config,
        ))
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["final_result"]["expert_count"], 2)
        artifacts = [
            entry for entry in blackboard.entries.values()
            if entry.kind is EntryKind.ARTIFACT
        ]
        self.assertTrue(artifacts, "approved workflow must persist a deliverable artifact")
        self.assertTrue(any(
            entry.kind is EntryKind.DECISION and entry.status.value == "accepted"
            for entry in blackboard.entries.values()
        ))


if __name__ == "__main__":
    unittest.main()


class CoreBusinessAuthenticationNegativeE2ETests(unittest.TestCase):
    def test_invalid_login_unauthenticated_identity_and_csrf_rejection(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is installed in platform CI")

        identity = JourneyIdentity()
        app = FastAPI()
        app.add_middleware(
            IdentityASGIMiddleware, identity=identity,
            allowed_origins=("https://platform.example.test",),
        )
        app.include_router(create_identity_router(identity=identity))

        with TestClient(app, base_url="https://platform.example.test") as client:
            invalid_login = client.post("/auth/login", json={
                "email": "operator@example.test", "password": "wrong",
                "tenant_id": "tenant-e2e",
            })
            self.assertEqual(invalid_login.status_code, 401)
            self.assertNotIn("__Host-platform_session", client.cookies)

            unauthenticated_me = client.get("/auth/me")
            self.assertEqual(unauthenticated_me.status_code, 401)

            login = client.post("/auth/login", json={
                "email": "operator@example.test", "password": "valid",
                "tenant_id": "tenant-e2e",
            })
            self.assertEqual(login.status_code, 200)
            self.assertTrue(login.json()["authenticated"])

            # A cookie-authenticated state-changing request must not pass without
            # the matching CSRF header, even after a valid login.
            csrf = client.cookies.get("__Host-platform_csrf")
            self.assertTrue(csrf)
            rejected_logout = client.post("/auth/logout")
            self.assertEqual(rejected_logout.status_code, 403)

            accepted_logout = client.post(
                "/auth/logout", headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(accepted_logout.status_code, 200)
            self.assertFalse(accepted_logout.json()["authenticated"])
