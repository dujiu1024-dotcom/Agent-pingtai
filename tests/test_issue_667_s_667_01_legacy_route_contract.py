import re
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_platform.api.contract import ApiV1CompatibilityMiddleware

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_LEGACY_ROUTES = {
    "/api/v1/legacy/state",
    "/api/v1/legacy/output?path=",
    "/api/v1/legacy/roundtable",
    "/api/v1/legacy/selfcheck",
    "/api/v1/legacy/content",
    "/api/v1/legacy/finance",
    "/api/v1/legacy/sync",
    "/api/v1/legacy/subscriptions/toggle",
    "/api/v1/legacy/tasks/",
    "/api/v1/legacy/dispatch/diagnose",
    "/api/v1/legacy/agents/",
    "/api/v1/legacy/jobs",
    "/api/v1/legacy/tasks",
}

def test_legacy_console_api_calls_remain_on_versioned_api_surface():
    source = (ROOT / "console.html").read_text(encoding="utf-8")
    calls = set(re.findall(r"""(?:fetch|api)\(\s*['"]([^'"]+)['"]""", source))
    legacy_calls = {path for path in calls if path.startswith("/api/")}
    assert EXPECTED_LEGACY_ROUTES <= legacy_calls
    assert all(path.startswith("/api/v1/") for path in legacy_calls)
    assert "'X-API-Version': 'v1'" in source

def test_legacy_api_prefix_is_rewritten_and_query_is_preserved():
    seen = []
    async def capture(request):
        seen.append((request.scope["path"], request.scope["query_string"]))
        return JSONResponse({"ok": True})
    app = Starlette(routes=[Route("/api/platform/v1/legacy/state", capture)])
    app.add_middleware(ApiV1CompatibilityMiddleware)
    response = TestClient(app).get("/api/v1/legacy/state?since=abc")
    assert response.status_code == 200
    assert seen == [("/api/platform/v1/legacy/state", b"since=abc")]
    assert response.headers["x-api-version"] == "v1"

def test_unmapped_legacy_api_route_returns_stable_json_404():
    async def homepage(_request):
        return JSONResponse({"ok": True})
    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(ApiV1CompatibilityMiddleware)
    response = TestClient(app).get("/api/v1/legacy/not-mounted")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.headers["x-api-version"] == "v1"