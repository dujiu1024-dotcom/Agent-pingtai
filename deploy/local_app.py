"""本地 ASGI 入口：生产组合 + 部署侧 Run 执行 builder。

启动方式：``python -m uvicorn deploy.local_app:app``（工作目录为仓库根）。
"""
import os

from agent_platform.production import create_production_app

from deploy.local_runtime import build_run_execution
from deploy.local_storage import LocalFileObjectStorage

_storage_root = os.environ.get(
    "LOCAL_OBJECT_STORAGE_DIR", "/Users/longyi/.fde-local-data/files",
)

app = create_production_app(
    run_execution_builder=build_run_execution,
    session_storage=LocalFileObjectStorage(_storage_root),
    local_owner_email=os.environ.get("LOCAL_OWNER_EMAIL"),
    local_tenant_id=os.environ.get("LOCAL_TENANT_ID", "fde"),
)
