from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.scheduler import create_scheduler_router
from agent_platform.identity.models import Principal


class FakeSchedulerService:
    def __init__(self) -> None:
        self.tenants: list[str] = []

    async def list_jobs(self, tenant_id: str):
        self.tenants.append(tenant_id)
        return [{
            "job_id": "daily_brief",
            "name": "每日简报",
            "enabled": True,
            "status": "failed",
            "failure_count": 2,
            "last_message": "provider timeout",
        }]

    async def list_runs(self, tenant_id: str, limit: int = 50):
        self.tenants.append(tenant_id)
        return [{
            "run_id": "run-1",
            "job_id": "daily_brief",
            "status": "failed",
            "message": "provider timeout",
        }]

    def notifier_status(self):
        return {"configured": False, "reason": "not configured"}


def _client(
    service: FakeSchedulerService, *, raise_server_exceptions: bool = True,
) -> TestClient:
    app = FastAPI()
    app.include_router(create_scheduler_router(service=service))

    @app.middleware("http")
    async def attach_principal(request, call_next):
        request.state.__dict__["identity.principal"] = Principal(
            user_id="user-1",
            tenant_id="tenant-a",
            session_id="session-1",
            email="user@example.test",
            display_name="Test User",
        )
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_scheduler_read_api_returns_persisted_failure_details_for_current_tenant():
    service = FakeSchedulerService()
    client = _client(service)

    jobs = client.get("/api/platform/v1/scheduler/jobs")
    runs = client.get("/api/platform/v1/scheduler/runs?limit=20")

    assert jobs.status_code == 200
    assert jobs.json()["items"][0]["failure_count"] == 2
    assert jobs.json()["items"][0]["last_message"] == "provider timeout"
    assert runs.status_code == 200
    assert runs.json()["items"][0]["status"] == "failed"
    assert runs.json()["items"][0]["message"] == "provider timeout"
    assert service.tenants == ["tenant-a", "tenant-a"]


def test_scheduler_read_api_does_not_turn_backend_failure_into_empty_success():
    class UnavailableScheduler(FakeSchedulerService):
        async def list_jobs(self, tenant_id: str):
            raise RuntimeError("database unavailable")

    client = _client(UnavailableScheduler(), raise_server_exceptions=False)
    response = client.get("/api/platform/v1/scheduler/jobs")

    assert response.status_code == 500
