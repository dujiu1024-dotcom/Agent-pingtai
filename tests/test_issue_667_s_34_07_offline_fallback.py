from pathlib import Path
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.web.app import create_platform_web_router


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_fallback_serves_console_without_blocking_on_optional_cdn():
    app = FastAPI()
    app.include_router(create_platform_web_router())

    response = TestClient(app).get("/app/?legacy=1")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    html = response.text
    scripts = re.findall(r"<script\b([^>]*)>", html, flags=re.IGNORECASE)
    external = [attrs for attrs in scripts if "src=" in attrs]
    assert len(external) == 1
    assert "three.min.js" in external[0]
    assert re.search(r"\basync(?:\s|$)", external[0]), (
        "the optional 3D CDN script must not block parsing when the browser is offline"
    )


def test_legacy_console_keeps_panels_bootable_when_threejs_is_unavailable():
    html = (ROOT / "console.html").read_text(encoding="utf-8")

    assert "let THREE_OK = typeof THREE !== 'undefined';" in html
    assert "3D 模块未加载（需要访问 CDN）　面板功能仍可使用" in html
    boot = html[html.index("  function boot() {"):]
    assert "if (THREE_OK) {" in boot
    assert "load();" in boot
    assert "pollJobs();" in boot
    assert boot.index("load();") > boot.index("if (THREE_OK) {")
