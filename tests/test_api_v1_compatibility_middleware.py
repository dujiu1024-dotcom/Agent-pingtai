from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.contract import ApiV1CompatibilityMiddleware


def test_v1_alias_rewrites_to_platform_router_prefix():
    app = FastAPI()
    app.add_middleware(ApiV1CompatibilityMiddleware)

    @app.get("/api/platform/v1/sessions")
    async def sessions():
        return {"items": []}

    response = TestClient(app).get("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert response.headers["x-api-version"] == "v1"


def test_unknown_v1_route_returns_stable_compatibility_404():
    app = FastAPI()
    app.add_middleware(ApiV1CompatibilityMiddleware)

    response = TestClient(app).get("/api/v1/legacy/roundtable")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.headers["x-api-version"] == "v1"
