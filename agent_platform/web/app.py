from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

STATIC = Path(__file__).with_name("static")
LEGACY_CONSOLE = Path(__file__).resolve().parents[2] / "console.html"
ASSETS = {"app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}
_RESERVED_PREFIXES = (
    "/api/",
    "/app",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/metrics",
    "/static/",
    "/assets/",
    "/.well-known/",
)


def _legacy_console_response() -> FileResponse:
    if not LEGACY_CONSOLE.is_file():
        raise HTTPException(503, "legacy console fallback is unavailable")
    return FileResponse(
        LEGACY_CONSOLE,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def create_platform_web_router() -> APIRouter:
    router = APIRouter(tags=["platform-web"])

    @router.get("/app", include_in_schema=False)
    @router.get("/app/", include_in_schema=False)
    async def index(legacy: bool = False):
        # Explicit opt-in keeps the legacy UI available during an incomplete
        # deployment. Missing new UI files also fail over without a dead link.
        if legacy or not (STATIC / "index.html").is_file():
            return _legacy_console_response()
        return FileResponse(
            STATIC / "index.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/app/assets/{name}", include_in_schema=False)
    async def asset(name: str):
        if name not in ASSETS or not (STATIC / name).is_file():
            raise HTTPException(404, "asset not found")
        return FileResponse(STATIC / name, media_type=ASSETS[name])

    @router.get("/{legacy_path:path}", include_in_schema=False)
    async def legacy_404_compatibility(request: Request, legacy_path: str):
        """Redirect browser page misses to the explicit legacy UI compatibility route.

        API, operational, app/static and file-like requests retain a real 404;
        this must never turn an API miss into an HTML response.
        """
        path = request.url.path
        if (
            path.startswith(_RESERVED_PREFIXES)
            or path in {"/", "/docs", "/redoc", "/openapi.json"}
            or Path(legacy_path).suffix
            or "text/html" not in request.headers.get("accept", "").lower()
        ):
            raise HTTPException(404, "not found")
        return RedirectResponse(
            url="/app/?legacy=1",
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    return router
