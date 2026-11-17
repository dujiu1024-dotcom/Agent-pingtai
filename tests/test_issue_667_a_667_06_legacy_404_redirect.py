from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.web.app import create_platform_web_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_platform_web_router())
    return TestClient(app, follow_redirects=False)


def test_unknown_browser_page_redirects_to_legacy_console() -> None:
    response = _client().get(
        "/old-dashboard/clients",
        headers={"accept": "text/html,application/xhtml+xml"},
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/app/?legacy=1"
    assert response.headers["cache-control"] == "no-store"


def test_unknown_api_path_remains_404_even_for_html_accept() -> None:
    response = _client().get(
        "/api/v1/legacy/unknown",
        headers={"accept": "text/html"},
    )
    assert response.status_code == 404
    assert "location" not in response.headers


def test_unknown_file_and_non_browser_requests_remain_404() -> None:
    client = _client()
    file_response = client.get(
        "/old-dashboard/missing.js",
        headers={"accept": "text/html"},
    )
    api_like_response = client.get(
        "/old-dashboard/clients",
        headers={"accept": "application/json"},
    )
    assert file_response.status_code == 404
    assert api_like_response.status_code == 404
