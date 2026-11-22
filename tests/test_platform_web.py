
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.web.app import create_platform_web_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_platform_web_router())
    return TestClient(app)


def test_platform_web_serves_agent_platform_workbench_by_default():
    client = _client()
    response = client.get("/app/")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert 'id="legacy-console-fallback"' in response.text
    assert 'src="/app/assets/app.js"' in response.text
    assert "<title>Agent 平台</title>" in response.text
    assert response.headers["cache-control"] == "no-store"

    # The checked-in production bundle must expose the appended menu too;
    # source-only navigation changes are not sufficient for the served UI.
    bundle = client.get("/app/assets/app.js")
    stylesheet_path = re.search(r'href="([^"]+\\.css)"', response.text)
    assert stylesheet_path is not None
    stylesheet = client.get(stylesheet_path.group(1))
    assert bundle.status_code == 200
    assert "新增业务" in bundle.text
    assert "Agent 运行态" in bundle.text
    assert stylesheet.status_code == 200
    assert ".prototype-nav-section-label" in stylesheet.text


def test_platform_web_keeps_react_workbench_available_with_explicit_modern_flag():
    response = _client().get("/app/?modern=1")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert 'id="legacy-console-fallback"' in response.text
    assert response.headers["cache-control"] == "no-store"


def test_platform_web_serves_legacy_console_only_when_explicitly_requested():
    response = _client().get("/app/?legacy=1")
    assert response.status_code == 200
    assert "FDE 工作室" in response.text
    assert '<script type="module" src="/app/assets/legacy-console.js">' in response.text
    assert response.headers["cache-control"] == "no-store"


def test_legacy_console_bookmark_redirects_to_explicit_compatibility_route():
    response = _client().get("/console.html", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/app/?legacy=1"
    assert response.headers["cache-control"] == "no-store"


def test_platform_web_explicitly_serves_modular_legacy_console():
    client = _client()
    response = client.get("/app/?legacy=1")
    assert response.status_code == 200
    assert "FDE 工作室" in response.text
    assert '<script type="module" src="/app/assets/legacy-console.js">' in response.text
    assert '<script async src="https://cdnjs.cloudflare.com/ajax/libs/three.js/' in response.text
    assert response.headers["cache-control"] == "no-store"

    console_module = client.get("/app/assets/legacy-console.js")
    config_module = client.get("/app/assets/legacy-console-config.js")
    assert console_module.status_code == 200
    assert "from './legacy-console-config.js'" in console_module.text
    assert config_module.status_code == 200
    assert "export const ROLE_META" in config_module.text


def test_platform_web_falls_back_to_legacy_when_new_index_is_missing(tmp_path, monkeypatch):
    from agent_platform.web import app as web_app

    monkeypatch.setattr(web_app, "STATIC", tmp_path)
    response = _client().get("/app/")
    assert response.status_code == 200
    assert "FDE 工作室" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_platform_web_returns_503_when_both_frontends_are_missing(tmp_path, monkeypatch):
    from agent_platform.web import app as web_app

    monkeypatch.setattr(web_app, "STATIC", tmp_path)
    monkeypatch.setattr(web_app, "LEGACY_CONSOLE", tmp_path / "console.html")
    response = _client().get("/app/")
    assert response.status_code == 503


def test_platform_web_rejects_unknown_and_traversal_assets():
    client = _client()
    assert client.get("/app/assets/not-allowed.js").status_code == 404
    assert client.get("/app/assets/%2e%2e%2fconsole.html").status_code == 404


def test_platform_web_spa_fallback_uses_legacy_when_index_is_missing(tmp_path, monkeypatch):
    from agent_platform.web import app as web_app

    monkeypatch.setattr(web_app, "STATIC", tmp_path)
    response = _client().get("/app/workbench/batch")
    assert response.status_code == 200
    assert "FDE 工作室" in response.text
