"""运营看板静态页（dashboard.html），数据全部来自平台 API。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[2]
BOARD_HTML = ROOT / "dashboard.html"

# 看板是自包含的 <script>/<style>；路由自带 CSP，identity 中间件检测到已存在就不再覆盖。
BOARD_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; font-src 'self' data:; frame-ancestors 'none'"
)


def create_board_page_router() -> APIRouter:
    router = APIRouter(tags=["platform-board"])

    @router.get("/board", include_in_schema=False)
    @router.get("/board/", include_in_schema=False)
    async def board_page() -> FileResponse:
        return FileResponse(
            BOARD_HTML,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store", "Content-Security-Policy": BOARD_CSP},
        )

    return router
