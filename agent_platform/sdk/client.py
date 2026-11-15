"""Small synchronous Python SDK for the versioned platform API."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_VERSION = "v1"


@dataclass(frozen=True)
class ApiResponse:
    status_code: int
    headers: Mapping[str, str]
    body: Any


class Transport(Protocol):
    def send(self, request: Request, *, timeout: float) -> ApiResponse: ...


class UrllibTransport:
    def send(self, request: Request, *, timeout: float) -> ApiResponse:
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                body = json.loads(raw) if raw else None
                return ApiResponse(response.status, dict(response.headers.items()), body)
        except HTTPError as exc:
            raw = exc.read()
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = {"detail": raw.decode("utf-8", errors="replace")[:2000]}
            return ApiResponse(exc.code, dict(exc.headers.items()), body)
        except URLError as exc:
            raise ConnectionError("platform API request failed") from exc


class PlatformApiError(RuntimeError):
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"platform API returned HTTP {status_code}")


class PlatformClient:
    def __init__(self, *, base_url: str, token: str,
                 transport: Transport | None = None, timeout: float = 15.0) -> None:
        normalized = base_url.rstrip("/")
        if not normalized.startswith("https://"):
            raise ValueError("platform base_url must use HTTPS")
        if not token or timeout <= 0 or timeout > 120:
            raise ValueError("token and a timeout in (0, 120] are required")
        self.base_url = normalized
        self.token = token
        self.transport = transport or UrllibTransport()
        self.timeout = timeout

    def request(self, method: str, path: str, *,
                body: Mapping[str, Any] | None = None,
                idempotency_key: str | None = None) -> Any:
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise ValueError("API path must be an absolute platform-relative path")
        url = f"{self.base_url}/api/platform/{API_VERSION}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(url, data=data, headers=headers, method=method.upper())
        response = self.transport.send(request, timeout=self.timeout)
        if not 200 <= response.status_code < 300:
            raise PlatformApiError(response.status_code, response.body)
        return response.body

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Mapping[str, Any], *,
             idempotency_key: str | None = None) -> Any:
        return self.request("POST", path, body=body, idempotency_key=idempotency_key)
