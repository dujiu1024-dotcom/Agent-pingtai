"""Compatibility launcher for the archived legacy console.py implementation."""
import importlib.abc
import importlib.util
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent
_ARCHIVE = _ROOT / "legacy" / "v1-archive"
sys.path.insert(0, str(_ARCHIVE))


class _ArchivedLoader(importlib.abc.Loader):
    """从归档目录读源码，但把 __file__ 伪装成根目录路径。

    归档模块导入时按 __file__ 推导 ROOT，若保持归档路径会让 db/agency 等
    模块把数据库与配置目录解析到 legacy/v1-archive 下的副本。
    """

    def __init__(self, source: Path, display: Path) -> None:
        self._source = source
        self._display = display

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        module.__file__ = str(self._display)
        exec(compile(self._source.read_text(encoding="utf-8"),
                     str(self._display), "exec"), module.__dict__)


class _ArchivedFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if "." in fullname:
            return None
        source = _ARCHIVE / f"{fullname}.py"
        if not source.is_file():
            return None
        loader = _ArchivedLoader(source, _ROOT / f"{fullname}.py")
        return importlib.util.spec_from_loader(fullname, loader)


sys.meta_path.insert(0, _ArchivedFinder())

# 页面已统一走 /api/v1/legacy/*；归档控制台只实现了 /api/*。
# 二者是同一处理函数，这里做等价别名，避免页面显示“离线”。
from http.server import BaseHTTPRequestHandler  # noqa: E402

_LEGACY_PREFIX = "/api/v1/legacy/"
_parse_request = BaseHTTPRequestHandler.parse_request


def _parse_request_with_alias(self):  # noqa: ANN001
    accepted = _parse_request(self)
    path = getattr(self, "path", "")
    if path.startswith(_LEGACY_PREFIX):
        self.path = "/api/" + path[len(_LEGACY_PREFIX):]
    return accepted


BaseHTTPRequestHandler.parse_request = _parse_request_with_alias

_TARGET = _ARCHIVE / "console.py"
_SOURCE = _TARGET.read_text(encoding="utf-8")
# Compile with the original root path as __file__ so legacy ROOT/data paths stay stable.
exec(compile(_SOURCE, str(Path(__file__).resolve()), "exec"), globals(), globals())
