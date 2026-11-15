from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.identity import create_identity_router
from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.identity.http import IdentityASGIMiddleware
from agent_platform.identity.models import IssuedSession, Principal
from agent_platform.identity.service import AuthenticationError
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.service import InMemoryMemoryRepository, MemoryService


class E2EFakeIdentity:
    def __init__(self):
        self.principals = {
            "token-a": Principal("user-a", "tenant-a", "session-a", "a@example.test", "A"),
            "token-b": Principal("user-b", "tenant-b", "session-b", "b@example.test", "B"),
        }

    async def login(self, *, email, password, tenant_id, session_ttl):
        if password != "valid" or email not in {"a@example.test", "b@example.test"}:
            raise AuthenticationError("invalid")
        token = "token-a" if tenant_id == "tenant-a" else "token-b"
        user = "user-a" if tenant_id == "tenant-a" else "user-b"
        return IssuedSession(
            session_id=f"session-{user}", tenant_id=tenant_id, user_id=user,
            access_token=token, expires_at=datetime.now(timezone.utc) + session_ttl,
        )

    async def authenticate(self, token):
        if token not in self.principals:
            raise AuthenticationError("invalid")
        return self.principals[token]

    async def authenticate_api_credential(self, token):
        raise AuthenticationError("invalid")

    async def logout(self, principal):
        return True


class PlatformE2EReleaseGateTests(unittest.TestCase):
    def test_authenticated_login_knowledge_upload_rag_and_tenant_isolation(self):
        identity = E2EFakeIdentity()
        knowledge = KnowledgeService(InMemoryKnowledgeRepository())
        memory = MemoryService(InMemoryMemoryRepository())

        async def resolve_knowledge(request):
            principal = request.state.identity.principal
            return KnowledgePrincipal(
                tenant_id=principal.tenant_id, user_id=principal.user_id,
                project_ids=frozenset(), permissions=frozenset({"knowledge.manage"}),
            )

        class GroundedGenerator:
            async def generate(self, *, query, context):
                return {
                    "answer": "Evidence-backed answer.",
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
            resolve_knowledge_principal=resolve_knowledge,
            rag=RAGService(knowledge=knowledge, generator=GroundedGenerator()),
        ))
        with TestClient(app, base_url="https://platform.example.test") as client:
            login = client.post("/auth/login", json={
                "email": "a@example.test", "password": "valid", "tenant_id": "tenant-a",
            })
            self.assertEqual(login.status_code, 200)
            created = client.post("/api/platform/v1/knowledge/documents", json={
                "title": "Runbook", "scope": "tenant",
                "content": "The deployment runbook requires approval before production changes.",
            })
            self.assertEqual(created.status_code, 200)
            answer = client.post("/api/platform/v1/knowledge/answer", json={
                "query": "What is required before production changes?",
            })
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer.json()["answer"], "Evidence-backed answer.")
            self.assertTrue(answer.json()["citations"])

            # A separately authenticated tenant cannot retrieve tenant-a's document.
            client.cookies.clear()
            login_b = client.post("/auth/login", json={
                "email": "b@example.test", "password": "valid", "tenant_id": "tenant-b",
            })
            self.assertEqual(login_b.status_code, 200)
            results = client.get(
                "/api/platform/v1/knowledge/search", params={"q": "deployment runbook"},
            )
            self.assertEqual(results.status_code, 200)
            self.assertEqual(results.json()["items"], [])


if __name__ == "__main__":
    unittest.main()
