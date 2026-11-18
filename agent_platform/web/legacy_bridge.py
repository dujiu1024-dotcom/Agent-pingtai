"""新平台页面路由与旧版兼容桥。

运营看板与 3D 办公室均由新平台提供：看板数据来自新 API；3D 办公室沿用旧页面，
但状态接口已切到 ``/api/platform/v1/office/state``（PostgreSQL）。
``/api/v1/legacy/*`` 仅保留给归档页面内部调用，不再启动第二个 HTTP 服务。
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import io
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "legacy" / "v1-archive"
BOARD_HTML = ROOT / "dashboard.html"
OFFICE_HTML = ROOT / "console.html"

# 看板是自包含的 <script>/<style>；路由自带 CSP，identity 中间件检测到已存在就不再覆盖。
BOARD_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; font-src 'self' data:; frame-ancestors 'none'"
)
# 3D 办公室需要从 CDN 加载 three.js r128，其余与看板一致。
OFFICE_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
    "font-src 'self' data:; frame-ancestors 'none'"
)

_module: Any | None = None
_module_lock = threading.Lock()


class _ArchivedLoader(importlib.abc.Loader):
    """从归档目录读源码，但把 ``__file__`` 伪装成仓库根路径。"""

    def __init__(self, source: Path, display: Path) -> None:
        self._source = source
        self._display = display

    def create_module(self, spec):  # noqa: ANN001
        return None

    def exec_module(self, module) -> None:  # noqa: ANN001
        module.__file__ = str(self._display)
        exec(compile(self._source.read_text(encoding="utf-8"),
                     str(self._display), "exec"), module.__dict__)


class _ArchivedFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        if "." in fullname or fullname in sys.modules:
            return None
        source = ARCHIVE / f"{fullname}.py"
        if not source.is_file():
            return None
        return importlib.util.spec_from_loader(
            fullname, _ArchivedLoader(source, ROOT / f"{fullname}.py"),
        )


def _legacy() -> Any:
    """惰性导入归档控制台模块，保证 repo 根目录的台账/配置路径不变。"""
    global _module
    if _module is not None:
        return _module
    with _module_lock:
        if _module is not None:
            return _module
        if str(ARCHIVE) in sys.path:
            sys.path.remove(str(ARCHIVE))
        sys.path.insert(0, str(ARCHIVE))
        if not any(isinstance(f, _ArchivedFinder) for f in sys.meta_path):
            sys.meta_path.insert(0, _ArchivedFinder())
        spec = importlib.util.spec_from_loader(
            "fde_legacy_console",
            _ArchivedLoader(ARCHIVE / "console.py", ROOT / "console.py"),
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _module = module
        return module


def _handler_class(legacy: Any) -> type:
    class BridgeHandler(legacy.Handler):
        def __init__(self, *, method: str, path: str, headers: dict[str, str],
                     body: bytes, port: int, principal: dict[str, Any]) -> None:
            self.command = method
            self.path = path
            self.headers = headers
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.status = 200
            self.response_headers: list[tuple[str, str]] = []
            self.requestline = f"{method} {path} HTTP/1.1"
            self.client_address = ("127.0.0.1", 0)
            self.server = SimpleNamespace(server_address=("127.0.0.1", port))
            self._bridge_principal = principal

        # --- 已由平台身份中间件完成的检查 ---
        def _host_ok(self) -> bool:
            return True

        def _origin_ok(self) -> bool:
            return True

        def _principal(self):  # noqa: ANN001
            return self._bridge_principal

        # --- 内存响应 ---
        def send_response(self, code, message=None):  # noqa: ANN001
            self.status = int(code)

        def send_header(self, key, value):  # noqa: ANN001
            self.response_headers.append((str(key), str(value)))

        def end_headers(self) -> None:
            return None

        def log_message(self, fmt, *args):  # noqa: ANN001
            return None

    return BridgeHandler


def _principal_mapping(request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "identity.principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    name = getattr(principal, "display_name", None) or "创始人"
    return {"role": "founder", "projects": [], "name": name}


def _invoke(legacy: Any, method: str, raw_path: str,
            headers: dict[str, str], body: bytes,
            principal: dict[str, Any], port: int) -> Response:
    handler = _handler_class(legacy)(
        method=method, path=raw_path, headers=headers,
        body=body, port=port, principal=principal,
    )
    if method == "POST":
        handler.do_POST()
    else:
        handler.do_GET()

    outgoing = {key.lower(): value for key, value in handler.response_headers}
    media_type = outgoing.pop("content-type", "application/octet-stream").split(";")[0]
    outgoing.pop("content-length", None)
    return Response(
        content=handler.wfile.getvalue(),
        status_code=handler.status,
        media_type=media_type,
        headers=outgoing,
    )


def _page(path: Path, csp: str) -> FileResponse:
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"缺少页面文件：{path.name}")
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store", "Content-Security-Policy": csp},
    )


def create_legacy_bridge_router() -> APIRouter:
    legacy = _legacy()
    router = APIRouter(tags=["legacy-office"])

    @router.get("/office", include_in_schema=False)
    @router.get("/office/", include_in_schema=False)
    async def office_page() -> FileResponse:
        # 3D 办公室继续提供，状态改由 /api/platform/v1/office/state 提供。
        return _page(OFFICE_HTML, OFFICE_CSP)

    @router.get("/board", include_in_schema=False)
    @router.get("/board/", include_in_schema=False)
    async def board_page() -> FileResponse:
        return _page(BOARD_HTML, BOARD_CSP)

    # ApiV1CompatibilityMiddleware 会把 /api/v1/* 重写到 /api/platform/v1/*，
    # 因此兼容接口注册在重写后的真实路径上；页面仍请求 /api/v1/legacy/*。
    @router.api_route(
        "/api/platform/v1/legacy/{path:path}", methods=["GET", "POST"],
        include_in_schema=False,
    )
    async def legacy_api(path: str, request: Request) -> Response:
        principal = _principal_mapping(request)
        raw = "/api/" + path
        if request.url.query:
            raw += "?" + request.url.query
        headers = {
            "Host": request.headers.get("host", "127.0.0.1:8000"),
            "Content-Type": request.headers.get("content-type", "application/json"),
            "If-None-Match": request.headers.get("if-none-match", ""),
        }
        body = await request.body()
        headers["Content-Length"] = str(len(body))
        return await run_in_threadpool(
            _invoke, legacy, request.method, raw, headers, body, principal, 8000,
        )

    return router
