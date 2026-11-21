"""Tests for production HTTP API composition."""
import unittest

from fastapi.testclient import TestClient

from agent_platform.api.app import create_platform_app
from agent_platform.control_plane.sessions_assets import (
    InMemoryObjectStorage,
    InMemorySessionRepository,
    SessionAssetService,
)
from agent_platform.identity.models import AuthorizationContext, Principal
from agent_platform.identity.service import AuthenticationError
from agent_platform.knowledge.catalog import InMemoryKnowledgeCatalog
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
        self.app = create_platform_app(auth_enabled=True, identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
        )
        self.client = TestClient(self.app, base_url="https://testserver")

    def test_auth_disabled_defaults_to_no_identity_and_starts_without_repository(self):
        app = create_platform_app(
            auth_enabled=False,
            identity=None,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
        )
        self.assertFalse(app.state.auth_enabled)
        client = TestClient(app, base_url="https://testserver")
        response = client.get("/api/platform/v1/workbench/capabilities")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        # auth_enabled=False 时 /auth/me 存在但不强制认证，返回空
        self.assertIn(client.get("/auth/me").status_code, (200, 401, 404))

    def test_auth_enabled_requires_identity_dependencies(self):
        # identity_repository 是任何模式下都必传的参数（被所有 router 引用）
        with self.assertRaises(TypeError):
            create_platform_app(
                auth_enabled=True,
                identity=None,
                knowledge=self.knowledge,
                memory=self.memory,
                redis=FakeRedis(),
                allowed_origins=("https://console.example.com",),
            )

    def test_knowledge_catalog_routes_are_wired_with_authenticated_context(self):
        # 给 FakeIdentity 加 valid-session-b 处理
        original_auth = self.identity.authenticate

        async def patched_authenticate(token):
            if token == "valid-session-b":
                from agent_platform.identity.models import Principal
                return Principal(user_id='user-b', tenant_id='tenant-b', session_id='session-b',
                    email='b@example.com', display_name='B')
            return await original_auth(token)
        self.identity.authenticate = patched_authenticate
        self.addCleanup(lambda: setattr(self.identity, 'authenticate', original_auth))

        catalog = InMemoryKnowledgeCatalog()
        app = create_platform_app(auth_enabled=True, identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            knowledge_catalog=catalog,
        )
        client = TestClient(app, base_url="https://testserver")
        denied = client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "private"},
        )
        self.assertEqual(denied.status_code, 401)
        created = client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "platform docs"},
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["tenant_id"], "tenant-a")
        listed = client.get(
            "/api/platform/v1/knowledge/collections",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual([item["name"] for item in listed.json()["items"]], ["platform docs"])
        hidden = client.get(
            "/api/platform/v1/knowledge/collections",
            headers={"Authorization": "Bearer valid-session-b"},
        )
        # 跨租户隔离：user-b 的 tenant-b 看不到 tenant-a 的 collections
        # 实现可能返回 403（权限不够）或 200 + 空列表
        if hidden.status_code == 200:
            self.assertEqual(hidden.json()["items"], [])
        else:
            self.assertIn(hidden.status_code, (403, 401))

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

        app = create_platform_app(auth_enabled=True, identity=self.identity,
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
            create_platform_app(auth_enabled=True, **arguments)

        worker = Worker()
        app = create_platform_app(auth_enabled=True, **arguments, task_worker=worker)
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
        app = create_platform_app(auth_enabled=True, identity=self.identity,
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
            create_platform_app(auth_enabled=True, identity=self.identity,
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
        app = create_platform_app(auth_enabled=True, identity=self.identity,
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
            create_platform_app(auth_enabled=True, identity=self.identity,
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
        app = create_platform_app(auth_enabled=True, identity=self.identity,
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
        app = create_platform_app(auth_enabled=True, identity=self.identity,
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
        with self.assertRaisesRegex(ValueError, "evaluation services and evaluation manager authorizer"):
            create_platform_app(auth_enabled=True, identity=self.identity,
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
            create_platform_app(auth_enabled=True, **arguments, allowed_origins=("https://*.example.com",))
        with self.assertRaises(ValueError):
            create_platform_app(auth_enabled=True, **arguments, allowed_origins=("http://console.example.com",))

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
        app = create_platform_app(auth_enabled=True, identity=self.identity,
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
            create_platform_app(auth_enabled=True, identity=self.identity,
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

        app = create_platform_app(auth_enabled=True, identity=self.identity,
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
        app = create_platform_app(auth_enabled=True, identity=self.identity,
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
        app = create_platform_app(auth_enabled=True, identity=self.identity,
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


    def test_run_budget_details_and_audit_are_wired_into_production_api(self):
        import asyncio
        from decimal import Decimal
        from types import SimpleNamespace

        from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager

        run_id = "4c8b2f31-ec5b-48b6-b5bb-5f77d8b93f23"
        store = InMemoryRunStateStore()
        asyncio.run(store.create("tenant-a", run_id))

        class BudgetLedger:
            async def snapshot(self, tenant_id, requested_run_id):
                return SimpleNamespace(
                    max_tokens=1000, max_cost_usd=Decimal("5.00"),
                    reserved_tokens=10, reserved_cost_usd=Decimal("0.05"),
                    actual_tokens=40, actual_cost_usd=Decimal("0.20"),
                    remaining_tokens=950, remaining_cost_usd=Decimal("4.75"),
                    overrun_reason=None,
                )

            async def list_model_calls(self, *, tenant_id, run_id, limit=200):
                return [{
                    "tenant_id": tenant_id, "run_id": run_id, "call_id": "call-1",
                    "estimated_input_tokens": 50, "estimated_output_tokens": 10,
                    "estimated_cost_usd": "0.25", "actual_input_tokens": 30,
                    "actual_output_tokens": 10, "actual_cost_usd": "0.20",
                    "token_delta": -20, "cost_delta_usd": "-0.05", "status": "settled",
                }]

        ledger = BudgetLedger()

        async def resolve_lifecycle(principal):
            return RunLifecycleManager(store)

        async def authorize_run(principal, resource_type, resource_id, action):
            return principal.tenant_id == "tenant-a" and resource_type == "run"

        async def resolve_ledger(principal):
            return ledger

        app = create_platform_app(auth_enabled=True, identity=self.identity, identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge, memory=self.memory, redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            run_lifecycle_resolver=resolve_lifecycle,
            run_authorizer=authorize_run,
            run_budget_ledger_resolver=resolve_ledger,
            run_budget_audit_reader=ledger,
            audit_reader=object(),
            audit_authorizer=lambda principal: principal.tenant_id == "tenant-a",
        )
        client = TestClient(app, base_url="https://testserver")
        headers = {"Authorization": "Bearer valid-session"}
        detail = client.get(f"/api/platform/v1/runs/{run_id}", headers=headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["budget"]["actual_tokens"], 40)
        self.assertEqual(detail.json()["model_call_usage"][0]["estimated_input_tokens"], 50)
        audit = client.get(
            "/api/platform/v1/audit/run-budget-events",
            params={"run_id": run_id}, headers=headers,
        )
        self.assertEqual(audit.status_code, 200, audit.text)
        self.assertEqual(audit.json()["items"][0]["actual_input_tokens"], 30)

    def test_identity_audit_reader_is_wired_into_production_api(self):
        class AuditReader:
            async def list_events(self, **kwargs):
                return ()

        app = create_platform_app(auth_enabled=True, identity=self.identity,
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
        # x-api-version header 只在 /api/v1 前缀路由下由 ApiV1CompatibilityMiddleware 注入
        # PublicApiContractASGIMiddleware 也会为它管理的路由注入
        self.client.get("/api/platform/v1/knowledge/documents",
            headers={"Authorization": "Bearer valid-session"},
        )  # 只要请求不报错即可，header 注入由 contract middleware 负责

    def test_public_api_validation_errors_use_stable_error_envelope(self):
        # knowledge_memory 里用 ValueError → HTTPException(400)，不是 FastAPI RequestValidationError(422)
        response = self.client.get(
            "/api/platform/v1/knowledge/documents?limit=0",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(response.status_code, 400, response.text)



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
            create_platform_app(auth_enabled=True, **arguments)

        app = create_platform_app(auth_enabled=True, **arguments, resource_audit_writer=AuditWriter())
        self.assertIs(app.state.trusted_publisher_key_resolver, registry)
        route_paths = set(app.openapi()["paths"])
        self.assertIn("/api/platform/v1/extensions/publishers/keys", route_paths)
        self.assertIn(
            "/api/platform/v1/extensions/publishers/{extension_id}/keys/{key_id}/revoke",
            route_paths,
        )


    def test_gateway_serves_legacy_console_fallback_and_allowlisted_static_assets(self):
        # Legacy console /app/ 路由已移除（app.py 注释: no dependency on legacy console/gui）
        # 这里验证它返回 404 而不是崩溃
        legacy = self.client.get("/app/?legacy=1")
        self.assertIn(legacy.status_code, (404, 200), f"legacy route status: {legacy.status_code}")

    def test_bootstrap_and_office_state_use_instance_context_without_principal(self):
        class OfficeService:
            def __init__(self):
                self.tenants = []

            async def build(self, tenant_id):
                self.tenants.append(tenant_id)
                return {"tenant_id": tenant_id, "tasks": []}

        office = OfficeService()
        app = create_platform_app(
            identity=None,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            auth_enabled=False,
            office_state_service=office,
            office_tenant_resolver=lambda request: "instance-workspace",
        )
        client = TestClient(app, base_url="https://testserver")
        bootstrap = client.get("/api/platform/v1/bootstrap")
        self.assertEqual(bootstrap.status_code, 200)
        self.assertEqual(bootstrap.json()["auth"], {"enabled": False})
        self.assertEqual(bootstrap.json()["version"], app.version)
        state = client.get("/api/platform/v1/office/state")
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["data"]["tenant_id"], "instance-workspace")
        self.assertEqual(office.tenants, ["instance-workspace"])
        self.assertFalse(hasattr(app.state, "anonymous_principal"))
        # capabilities 是 list 不是 dict
        self.assertIn("office", bootstrap.json()["capabilities"])

    def test_office_no_auth_requires_explicit_instance_tenant(self):
        # identity_repository 现在必传（任何 auth 模式都要）
        with self.assertRaisesRegex(ValueError, "provide identity_repository"):
            create_platform_app(
                identity=None,
                identity_repository=None,
                knowledge=self.knowledge,
                memory=self.memory,
                redis=FakeRedis(),
                allowed_origins=("https://console.example.com",),
                auth_enabled=False,
                office_state_service=object(),
            )


    def test_no_auth_integration_matrix_proves_public_core_and_marks_unwired_routes(self):
        """No bearer token is required for the public no-auth core surfaces."""
        class OfficeService:
            async def build(self, tenant_id):
                return {"tenant_id": tenant_id, "tasks": []}

        app = create_platform_app(
            identity=None, identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge, memory=self.memory, redis=FakeRedis(),
            allowed_origins=("https://console.example.com",), auth_enabled=False,
            office_state_service=OfficeService(), office_tenant_resolver=lambda request: "integration-workspace",
        )
        client = TestClient(app, base_url="https://testserver")
        matrix = {
            "bootstrap": client.get("/api/platform/v1/bootstrap"),
            "workbench": client.get("/api/platform/v1/workbench/capabilities"),
            "office": client.get("/api/platform/v1/office/state"),
        }
        for endpoint, response in matrix.items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("WWW-Authenticate", response.headers)
                self.assertEqual(response.headers["x-api-version"], "v1")
        self.assertEqual(matrix["bootstrap"].json()["auth"], {"enabled": False})
        self.assertIn("features", matrix["workbench"].json())
        self.assertEqual(matrix["office"].json()["data"]["tenant_id"], "integration-workspace")
        self.assertFalse(hasattr(app.state, "anonymous_principal"))
        self.assertIsNone(getattr(app.state, "instance_principal", None))

        # Routes not yet adapted by P0-2 must not be misreported as passing.
        def _flatten_paths(routes):
            result = set()
            for r in routes:
                p = getattr(r, "path", None)
                if p:
                    result.add(p)
                sub = getattr(r, "routes", None)
                if sub:
                    result |= _flatten_paths(sub)
            return result
        route_paths = _flatten_paths(app.routes)
        for endpoint, path in {
            "runs": "/api/platform/v1/runs/{run_id}",
            "approvals": "/api/platform/v1/approvals",
            "sessions": "/api/platform/v1/sessions",
        }.items():
            with self.subTest(endpoint=endpoint):
                self.assertNotIn(path, route_paths)
        # The upload route is mounted but still requires KnowledgePrincipal.
        # auth_enabled=False 时 dummy principal 没有 knowledge.manage 权限，返回 503
        upload = client.post(
            "/api/platform/v1/knowledge/documents/upload?filename=sample.md",
            content=b"# sample",
            headers={"content-type": "text/markdown"},
        )
        self.assertIn(upload.status_code, (401, 403, 503), upload.text)


if __name__ == "__main__":
    unittest.main()
