"""Compatibility launcher for the archived legacy listener.py implementation."""
import importlib.abc
import importlib.util
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent
_ARCHIVE = _ROOT / "legacy" / "v1-archive"
sys.path.insert(0, str(_ARCHIVE))


class _ArchivedLoader(importlib.abc.Loader):
    """从归档目录读源码，但把 __file__ 伪装成根目录路径。"""

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

_TARGET = _ARCHIVE / "listener.py"
_SOURCE = _TARGET.read_text(encoding="utf-8")
# Compile with the original root path as __file__ so legacy ROOT/data paths stay stable.
exec(compile(_SOURCE, str(Path(__file__).resolve()), "exec"), globals(), globals())
