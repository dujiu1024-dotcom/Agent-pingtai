from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.web.app import create_platform_web_router


def test_platform_web_serves_react_app_index():
    app = FastAPI()
    app.include_router(create_platform_web_router())
    client = TestClient(app)

    page = client.get("/app/")
    assert page.status_code == 200
    assert "Agent Platform" in page.text
    assert "console.py" not in page.text
    assert page.headers["cache-control"] == "no-store"


def test_platform_web_rejects_unknown_asset():
    app = FastAPI()
    app.include_router(create_platform_web_router())
    assert TestClient(app).get("/app/assets/not-allowed.js").status_code == 404


def test_platform_web_ignores_legacy_console_query():
    """旧控制台已下线：legacy=1 不再跳转旧页面，统一返回平台自身前端。"""
    app = FastAPI()
    app.include_router(create_platform_web_router())
    client = TestClient(app)

    response = client.get("/app/?legacy=1")
    assert response.status_code == 200
    assert "FDE 工作室" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_platform_web_returns_service_unavailable_when_frontend_build_is_missing(
        tmp_path, monkeypatch):
    from agent_platform.web import app as web_app

    monkeypatch.setattr(web_app, "STATIC", tmp_path)
    app = FastAPI()
    app.include_router(create_platform_web_router())

    response = TestClient(app).get("/app/")
    assert response.status_code == 503
