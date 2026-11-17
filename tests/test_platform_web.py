from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_platform.web.app import create_platform_web_router

def test_platform_web_serves_independent_assets():
    app = FastAPI()
    app.include_router(create_platform_web_router())
    client = TestClient(app)
    page = client.get("/app/")
    assert page.status_code == 200
    assert "Agent Platform" in page.text
    assert "console.py" not in page.text
    assert client.get("/app/assets/app.js").status_code == 200
    assert "workbench/capabilities" in client.get("/app/assets/app.js").text
    assert client.get("/app/assets/style.css").status_code == 200

def test_platform_web_rejects_unknown_asset():
    app = FastAPI()
    app.include_router(create_platform_web_router())
    assert TestClient(app).get("/app/assets/not-allowed.js").status_code == 404
