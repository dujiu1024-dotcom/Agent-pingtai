"""Tests for production HTTP API composition."""
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_platform.api.app import create_platform_app
from agent_platform.control_plane.sessions_assets import (
    InMemoryObjectStorage,
    InMemorySessionRepository,
    SessionAssetService,
)
from agent_platform.identity.models import AuthorizationContext, Principal
from agent_platform.identity.service import AuthenticationError
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.service import InMemoryMemoryRepository, MemoryService


class FakeIdentity:
    async def authenticate(self, token):
        if token == "valid-session":
            return Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="session-a",
                email="a@example.com", display_name="A",
            )
        if token == "valid-session-b":
            return Principal(
                user_id="user-b", tenant_id="tenant-b", session_id="session-b",
                email="b@example.com", display_name="B",
            )
        raise AuthenticationError("invalid")

    async def authenticate_api_credential(self, token):
        raise AuthenticationError("invalid")


class FakeIdentityRepository:
    async def get_authorization_context(self, *, tenant_id, user_id):
        if tenant_id != "tenant-a" or user_id != "user-a":
            return None
        return AuthorizationContext(
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_roles=frozenset({"tenant_admin"}),
            effective_role_ids=frozenset({"tenant_admin"}),
            tenant_permissions=frozenset({"knowledge.manage", "memory.manage"}),
            project_ids=frozenset(),
            knowledge_manage_project_ids=frozenset(),
            memory_manage_project_ids=frozenset(),
            agent_ids=frozenset(),
            memory_manage_agent_ids=frozenset(),
        )


class FakeRedis:
    async def eval(self, *args):
        return 1


class PlatformApiCompositionTests(unittest.TestCase):
    def setUp(self):
        self.identity = FakeIdentity()
        self.knowledge = KnowledgeService(InMemoryKnowledgeRepository())
        self.memory = MemoryService(InMemoryMemoryRepository())
        self.app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
        )
        self.client = TestClient(self.app, base_url="https://testserver")

    def test_evaluation_gate_is_wired_through_platform_composition(self):
        class Governance:
            async def decide_release(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    evidence_id="evidence-1", passed=True, failures=(),
                    policy_id="production", policy_version=2,
                    comparison_id="comparison-1", content_sha256="a" * 64,
                )

        governance = Governance()
        app = create_platform_app(
            identity=self.identity, identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge, memory=self.memory, redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            evaluation_governance_service=governance,
            evaluation_gate_manage_authorizer=lambda principal: True,
        )
        client = TestClient(app, base_url="https://testserver")
        response = client.post(
            "/api/platform/v1/observability/evaluations/run-1/gate",
            headers={"Authorization": "Bearer valid-session"},
            json={
                "policy_id": "production", "policy_version": 2,
                "minimums": {"answer_term_f1": 0.9},
                "maximums": {"latency_ms": 500},
                "evidence_refs": ["ci://run/123"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["evidence_id"], "evidence-1")
        self.assertEqual(governance.kwargs["tenant_id"], "tenant-a")
        self.assertEqual(governance.kwargs["candidate_run_id"], "run-1")

    def test_evaluation_gate_rejects_actor_without_gate_permission(self):
        class Governance:
            async def decide_release(self, **kwargs):
                raise AssertionError("unauthorized actor must not reach governance service")

        app = create_platform_app(
            identity=self.identity, identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge, memory=self.memory, redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            evaluation_governance_service=Governance(),
            evaluation_gate_manage_authorizer=lambda principal: False,
        )
        response = TestClient(app, base_url="https://testserver").post(
            "/api/platform/v1/observability/evaluations/run-1/gate",
            headers={"Authorization": "Bearer valid-session"},
            json={"policy_id": "production", "policy_version": 1,
                  "minimums": {"answer_term_f1": 0.9},
                  "evidence_refs": ["ci://run/123"]},
        )
        self.assertEqual(response.status_code, 403)

    def test_evaluation_gate_requires_explicit_gate_authorizer(self):
        with self.assertRaisesRegex(ValueError, "gate manager authorizer"):
            create_platform_app(
                identity=self.identity, identity_repository=FakeIdentityRepository(),
                knowledge=self.knowledge, memory=self.memory, redis=FakeRedis(),
                allowed_origins=("https://console.example.com",),
                evaluation_governance_service=object(),
            )

    def test_business_routes_require_authenticated_session(self):
        response = self.client.get("/api/platform/v1/knowledge/documents")
        self.assertEqual(response.status_code, 401)

    def test_authenticated_session_is_resolved_into_business_principal(self):
        response = self.client.get(
            "/api/platform/v1/knowledge/documents",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_auth_me_and_security_headers_are_available(self):
        response = self.client.get(
            "/auth/me",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], "user-a")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_file_assets_are_isolated_by_tenant_in_authenticated_api(self):
        service = SessionAssetService(
            InMemorySessionRepository(), InMemoryObjectStorage(),
        )

        async def resolve_service(principal):
            return service

        async def authorize(principal, resource_type, resource_id, action):
            return True

        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            session_service_resolver=resolve_service,
            session_authorizer=authorize,
        )
        client = TestClient(app, base_url="https://testserver")
        uploaded = client.post(
            "/api/platform/v1/files",
            content=b"tenant-a secret",
            headers={
                "Authorization": "Bearer valid-session",
                "Content-Type": "text/plain",
                "X-Filename": "secret.txt",
            },
        )
        self.assertEqual(uploaded.status_code, 201)
        asset_id = uploaded.json()["asset_id"]

        same_tenant = client.get(
            f"/api/platform/v1/files/{asset_id}",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(same_tenant.status_code, 200)
        self.assertEqual(same_tenant.content, b"tenant-a secret")

        other_tenant = client.get(
            f"/api/platform/v1/files/{asset_id}",
            headers={"Authorization": "Bearer valid-session-b"},
        )
        self.assertEqual(other_tenant.status_code, 404)

    def test_workbench_execution_requires_and_starts_durable_worker(self):
        class Worker:
            def __init__(self):
                self.started = False
                self.stopped = False

            async def start(self):
                self.started = True

            async def stop(self):
                self.stopped = True

        async def resolve_task_service(principal):
            return object()

        async def authorize_task(principal, resource_id, action):
            return True

        arguments = {
            "identity": self.identity,
            "identity_repository": FakeIdentityRepository(),
            "knowledge": self.knowledge,
            "memory": self.memory,
            "redis": FakeRedis(),
            "allowed_origins": ("https://console.example.com",),
            "task_service_resolver": resolve_task_service,
            "task_authorizer": authorize_task,
        }
        with self.assertRaisesRegex(ValueError, "durable task worker"):
            create_platform_app(**arguments)

        worker = Worker()
        app = create_platform_app(**arguments, task_worker=worker)
        with TestClient(app, base_url="https://testserver") as client:
            response = client.get(
                "/api/platform/v1/workbench/capabilities",
                headers={"Authorization": "Bearer valid-session"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["features"]["task_execution"])
            self.assertTrue(worker.started)
        self.assertTrue(worker.stopped)

    def test_memory_expiry_worker_is_started_and_stopped_by_app_lifecycle(self):
        class Worker:
            def __init__(self):
                self.started = False
                self.stopped = False

            async def start(self):
                self.started = True

            async def stop(self):
                self.stopped = True

        worker = Worker()
        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            memory_expiry_worker=worker,
        )
        with TestClient(app, base_url="https://testserver"):
            self.assertTrue(worker.started)
        self.assertTrue(worker.stopped)

    def test_memory_expiry_worker_must_expose_lifecycle_methods(self):
        class InvalidWorker:
            async def start(self):
                pass

        with self.assertRaisesRegex(ValueError, "memory_expiry_worker"):
            create_platform_app(
                identity=self.identity,
                identity_repository=FakeIdentityRepository(),
                knowledge=self.knowledge,
                memory=self.memory,
                redis=FakeRedis(),
                allowed_origins=("https://console.example.com",),
                memory_expiry_worker=InvalidWorker(),
            )

    def test_outbox_relay_worker_is_started_and_stopped_with_api(self):
        class Worker:
            def __init__(self):
                self.started = False
                self.stopped = False

            async def start(self):
                self.started = True

            async def stop(self):
                self.stopped = True

        worker = Worker()
        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            outbox_relay_worker=worker,
        )
        with TestClient(app, base_url="https://testserver"):
            self.assertTrue(worker.started)
        self.assertTrue(worker.stopped)

    def test_factory_rejects_invalid_outbox_worker_lifecycle(self):
        class InvalidWorker:
            async def start(self):
                pass

        with self.assertRaisesRegex(ValueError, "outbox_relay_worker"):
            create_platform_app(
                identity=self.identity,
                identity_repository=FakeIdentityRepository(),
                knowledge=self.knowledge,
                memory=self.memory,
                redis=FakeRedis(),
                allowed_origins=("https://console.example.com",),
                outbox_relay_worker=InvalidWorker(),
            )

    def test_alert_evaluation_worker_is_started_and_stopped_with_app(self):
        class Worker:
            def __init__(self):
                self.started = False
                self.stopped = False

            async def start(self):
                self.started = True

            async def stop(self):
                self.stopped = True

        worker = Worker()
        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            alert_evaluation_worker=worker,
        )
        with TestClient(app, base_url="https://testserver"):
            self.assertTrue(worker.started)
        self.assertTrue(worker.stopped)

    def test_observability_routes_are_wired_through_platform_app(self):
        class AlertStore:
            def __init__(self):
                self.scopes = []

            async def list_open(self, *, scope_key, limit):
                self.scopes.append(scope_key)
                return [{"event_id": "alert-a"}]

        class EvaluationRepository:
            def __init__(self):
                self.saved = []
                self.listed = []

            async def save_human_score(self, **kwargs):
                self.saved.append(kwargs)
                return "score-a"

            async def list_human_scores(self, **kwargs):
                self.listed.append(kwargs)
                return [{"score_id": "score-a"}]

        alerts = AlertStore()
        evaluations = EvaluationRepository()
        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            alert_store=alerts,
            alert_manage_authorizer=lambda principal: principal.tenant_id == "tenant-a",
            evaluation_repository=evaluations,
            evaluation_manage_authorizer=lambda principal: principal.tenant_id == "tenant-a",
            evaluation_review_authorizer=lambda principal: principal.tenant_id == "tenant-a",
        )
        client = TestClient(app, base_url="https://testserver")
        headers = {"Authorization": "Bearer valid-session"}
        alerts_response = client.get(
            "/api/platform/v1/observability/alerts", headers=headers,
        )
        self.assertEqual(alerts_response.status_code, 200)
        self.assertEqual(alerts.scopes, ["tenant-a"])

        score_response = client.post(
            "/api/platform/v1/observability/evaluations/eval-a/scores",
            headers=headers,
            json={"case_id": "case-a", "score": 0.9, "rationale": "verified"},
        )
        self.assertEqual(score_response.status_code, 201)
        self.assertEqual(evaluations.saved[0]["tenant_id"], "tenant-a")
        listed = client.get(
            "/api/platform/v1/observability/evaluations/eval-a/scores",
            headers=headers,
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(evaluations.listed[0]["tenant_id"], "tenant-a")

    def test_observability_evaluation_dependencies_must_be_paired(self):
        with self.assertRaisesRegex(ValueError, "evaluation repository"):
            create_platform_app(
                identity=self.identity,
                identity_repository=FakeIdentityRepository(),
                knowledge=self.knowledge,
                memory=self.memory,
                redis=FakeRedis(),
                allowed_origins=("https://console.example.com",),
                evaluation_repository=object(),
            )

    def test_factory_rejects_wildcard_and_non_https_origins(self):
        arguments = {
            "identity": self.identity,
            "identity_repository": FakeIdentityRepository(),
            "knowledge": self.knowledge,
            "memory": self.memory,
            "redis": FakeRedis(),
        }
        with self.assertRaises(ValueError):
            create_platform_app(**arguments, allowed_origins=("https://*.example.com",))
        with self.assertRaises(ValueError):
            create_platform_app(**arguments, allowed_origins=("http://console.example.com",))

    def test_memory_extraction_event_worker_uses_app_lifecycle(self):
        class Worker:
            def __init__(self):
                self.started = False
                self.stopped = False

            async def start(self):
                self.started = True

            async def stop(self):
                self.stopped = True

        worker = Worker()
        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            memory_extraction_event_worker=worker,
        )
        with TestClient(app, base_url="https://testserver"):
            self.assertTrue(worker.started)
        self.assertTrue(worker.stopped)

    def test_memory_extraction_event_worker_requires_lifecycle_methods(self):
        with self.assertRaisesRegex(ValueError, "memory_extraction_event_worker"):
            create_platform_app(
                identity=self.identity,
                identity_repository=FakeIdentityRepository(),
                knowledge=self.knowledge,
                memory=self.memory,
                redis=FakeRedis(),
                allowed_origins=("https://console.example.com",),
                memory_extraction_event_worker=object(),
            )


    def test_prompt_registry_router_is_wired_with_explicit_authorizer(self):
        from agent_platform.observability.prompts import PromptRegistry

        class AsyncRegistry:
            def __init__(self):
                self.inner = PromptRegistry()

            async def register(self, prompt_id, template, *, created_by):
                return self.inner.register(prompt_id, template)

        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            prompt_registry=AsyncRegistry(),
            prompt_manage_authorizer=lambda principal, prompt_id, action: True,
        )
        client = TestClient(app, base_url="https://testserver")
        response = client.post(
            "/api/platform/v1/prompts/support/versions",
            json={"template": "Answer {question}"},
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["prompt_id"], "support")


    def test_evaluation_run_service_is_wired_with_tenant_authorization(self):
        from types import SimpleNamespace

        class EvaluationService:
            async def run(self, **kwargs):
                self.kwargs = kwargs
                report = SimpleNamespace(
                    dataset_id=kwargs["dataset_id"], dataset_version=kwargs["dataset_version"],
                    pass_rate=1.0, average_score=1.0, average_recall_at_k=None,
                )
                return SimpleNamespace(
                    eval_run_id="eval-run-1", report=report,
                    gate_passed=True, gate_failures=(),
                )

        service = EvaluationService()
        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            evaluation_repository=object(),
            evaluation_run_service=service,
            evaluation_manage_authorizer=lambda principal: True,
        )
        client = TestClient(app, base_url="https://testserver")
        response = client.post(
            "/api/platform/v1/observability/evaluations/run",
            json={
                "dataset_id": "release-smoke",
                "dataset_version": 3,
                "model_key": "provider/model@1",
            },
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["gate_passed"])
        self.assertEqual(service.kwargs["tenant_id"], "tenant-a")



    def test_quota_reservation_worker_is_started_and_stopped(self):
        class Worker:
            def __init__(self):
                self.started = False
                self.stopped = False

            async def start(self):
                self.started = True

            async def stop(self):
                self.stopped = True

        worker = Worker()
        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            quota_reservation_worker=worker,
        )
        with TestClient(app, base_url="https://testserver"):
            self.assertTrue(worker.started)
        self.assertTrue(worker.stopped)


    def test_identity_audit_reader_is_wired_into_production_api(self):
        class AuditReader:
            async def list_events(self, **kwargs):
                return ()

        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            audit_reader=AuditReader(),
            identity_audit_reader=AuditReader(),
            audit_authorizer=lambda principal: True,
        )
        client = TestClient(app, base_url="https://testserver")
        response = client.get(
            "/api/platform/v1/audit/identity-events",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"tenant_id": "tenant-a", "items": []})

    def test_version_header_is_emitted_and_unknown_version_is_rejected(self):
        success = self.client.get(
            "/api/platform/v1/knowledge/documents",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(success.status_code, 200)
        self.assertEqual(success.headers["x-api-version"], "v1")

        rejected = self.client.get(
            "/api/platform/v1/knowledge/documents",
            headers={"Authorization": "Bearer valid-session", "X-API-Version": "v2"},
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.headers["x-api-version"], "v1")
        self.assertEqual(rejected.json()["error"]["code"], "INVALID_REQUEST")
        self.assertNotIn("detail", rejected.json())

    def test_public_api_validation_errors_use_stable_error_envelope(self):
        response = self.client.get(
            "/api/platform/v1/knowledge/documents?limit=0",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(response.json()["error"]["message"], "Request validation failed")



    def test_publisher_key_registry_requires_authorizer_and_audit_and_is_exposed_to_runtime(self):
        class Registry:
            async def resolve(self, tenant_id, extension_id, key_id):
                return None

        class AuditWriter:
            async def record(self, **event):
                return None

        registry = Registry()
        arguments = {
            "identity": self.identity,
            "identity_repository": FakeIdentityRepository(),
            "knowledge": self.knowledge,
            "memory": self.memory,
            "redis": FakeRedis(),
            "allowed_origins": ("https://console.example.com",),
            "publisher_key_registry": registry,
            "publisher_key_authorizer": lambda principal, extension_id, action: True,
        }
        with self.assertRaisesRegex(ValueError, "append-only audit writer"):
            create_platform_app(**arguments)

        app = create_platform_app(**arguments, resource_audit_writer=AuditWriter())
        self.assertIs(app.state.trusted_publisher_key_resolver, registry)
        route_paths = {route.path for route in app.routes}
        self.assertIn("/api/platform/v1/extensions/publishers/keys", route_paths)
        self.assertIn(
            "/api/platform/v1/extensions/publishers/{extension_id}/keys/{key_id}/revoke",
            route_paths,
        )

if __name__ == "__main__":
    unittest.main()
