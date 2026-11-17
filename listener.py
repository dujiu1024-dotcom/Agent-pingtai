"""Compatibility launcher for the archived legacy listener.py implementation."""
from pathlib import Path
import sys

_ARCHIVE = Path(__file__).resolve().parent / "legacy" / "v1-archive"
sys.path.insert(0, str(_ARCHIVE))
_TARGET = _ARCHIVE / "listener.py"
_SOURCE = _TARGET.read_text(encoding="utf-8")
# Compile with the original root path as __file__ so legacy ROOT/data paths stay stable.
exec(compile(_SOURCE, str(Path(__file__).resolve()), "exec"), globals(), globals())
