from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.bootstrap import create_bootstrap_router
from agent_platform.api.office import create_office_router
from agent_platform.security.audit_http import AuditRequestASGIMiddleware


def test_bootstrap_exposes_only_public_mode_and_capabilities():
    app = FastAPI()
    app.include_router(create_bootstrap_router(auth_enabled=False, capabilities=("office", "runs")))
    response = TestClient(app).get("/api/platform/v1/bootstrap")
    assert response.status_code == 200
    assert response.json() == {
        "auth": {"enabled": False},
        "version": "1.0.0",
        "capabilities": ["office", "runs"],
    }


def test_office_router_uses_explicit_instance_tenant_without_principal():
    class Service:
        async def build(self, tenant_id):
            return {"tenant_id": tenant_id}

    app = FastAPI()
    app.include_router(create_office_router(
        service=Service(),
        resolve_tenant=lambda request: "instance-workspace",
    ))
    response = TestClient(app).get("/api/platform/v1/office/state")
    assert response.status_code == 200
    assert response.json()["data"]["tenant_id"] == "instance-workspace"


def test_audit_middleware_records_explicit_null_actor_for_no_auth():
    class Sink:
        def __init__(self):
            self.records = []

        async def record(self, **record):
            self.records.append(record)

    sink = Sink()
    app = FastAPI()

    @app.get("/probe")
    async def probe():
        return {"ok": True}

    wrapped = AuditRequestASGIMiddleware(
        app,
        sink=sink,
        resolve_actor=lambda principal: None,
        resolve_null_actor_tenant=lambda scope: "instance-workspace",
    )
    response = TestClient(wrapped).get("/probe")
    assert response.status_code == 200
    assert len(sink.records) == 2
    assert all(record["tenant_id"] == "instance-workspace" for record in sink.records)
    assert all(record["actor_id"] is None for record in sink.records)
