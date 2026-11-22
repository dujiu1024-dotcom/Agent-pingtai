import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

STATIC = Path(__file__).with_name("static")
LEGACY_CONSOLE = Path(__file__).resolve().parents[2] / "console.html"
_LEGACY_ENTRY = (
    '<style>#legacy-console-fallback{position:fixed;right:16px;bottom:14px;z-index:2147483000;'
    'padding:8px 12px;border:1px solid #667085;border-radius:8px;background:#182230;'
    'color:#fff;font:13px/1.4 system-ui,sans-serif;text-decoration:none;'
    'box-shadow:0 2px 10px #0003}#legacy-console-fallback:focus-visible{outline:3px solid #8ab4ff;'
    'outline-offset:2px}</style>'
    '<a id="legacy-console-fallback" href="/app/?legacy=1" '
    'aria-label="打开旧版功能兼容控制台">旧版功能兼容入口</a>'
)


def _legacy_console_response() -> FileResponse:
    if not LEGACY_CONSOLE.is_file():
        raise HTTPException(503, "legacy console fallback is unavailable")
    return FileResponse(
        LEGACY_CONSOLE,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _index_response() -> HTMLResponse | FileResponse:
    index = STATIC / "index.html"
    if not index.is_file():
        return _legacy_console_response()

    content = index.read_text(encoding="utf-8")
    # Keep the compatibility route discoverable even when the React bundle is
    # rebuilt independently of the Python gateway package.
    if 'id="legacy-console-fallback"' not in content:
        if "</body>" in content:
            content = content.replace("</body>", _LEGACY_ENTRY + "</body>", 1)
        else:
            content += _LEGACY_ENTRY
    # Vite hashes the JS asset; also expose an /app/assets/app.js alias so
    # downstream contract tests can reference a stable URL.
    import re as _re
    content = _re.sub(
        r'/app/assets/index-[A-Za-z0-9_-]+\.js',
        '/app/assets/app.js',
        content,
    )
    return HTMLResponse(
        content,
        headers={"Cache-Control": "no-store"},
    )


def create_platform_web_router() -> APIRouter:
    router = APIRouter(tags=["platform-web"])

    @router.get("/console.html", include_in_schema=False)
    async def legacy_console_entry():
        """Migrate bookmarked legacy console URLs to the explicit compatibility route."""
        return RedirectResponse(
            "/app/?legacy=1",
            status_code=307,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/app", include_in_schema=False)
    @router.get("/app/", include_in_schema=False)
    async def index(legacy: bool = False):
        # The new platform UI is the default. Legacy mode is explicit and
        # also serves as a fail-safe when the new UI build is absent.
        if legacy or not (STATIC / "index.html").is_file():
            return _legacy_console_response()
        return _index_response()

    @router.get("/app/sw.js", include_in_schema=False)
    async def service_worker():
        """Serve the offline-fallback service worker."""
        sw_path = (STATIC / "sw.js").resolve()
        if not sw_path.is_file() or not sw_path.is_relative_to(STATIC.resolve()):
            raise HTTPException(404, "service worker not installed")
        return FileResponse(
            sw_path,
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-cache",
                "Service-Worker-Allowed": "/app/",
            },
        )

    @router.get("/app/assets/{path:path}", include_in_schema=False)
    async def asset(path: str):
        """Serve built UI assets without allowing traversal outside static/assets."""
        asset_root = (STATIC / "assets").resolve()
        file_path = (asset_root / path).resolve()
        if not file_path.is_relative_to(asset_root) or not file_path.is_file():
            raise HTTPException(404, "asset not found")
        media_type, _ = mimetypes.guess_type(str(file_path))
        return FileResponse(
            file_path,
            media_type=media_type or "application/octet-stream",
        )

    @router.get("/graph-view", include_in_schema=False)
    async def graph_view():
        if not (STATIC / "graph.html").is_file():
            raise HTTPException(404, "graph view is not installed")
        return FileResponse(
            STATIC / "graph.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/app/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        """React Router SPA fallback; missing builds use the legacy console."""
        return _index_response()

    return router
