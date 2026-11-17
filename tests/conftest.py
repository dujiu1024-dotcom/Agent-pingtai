"""Expose archived v1 modules to legacy regression tests only.

Production code must import the new agent_platform package explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ARCHIVE = Path(__file__).resolve().parents[1] / "legacy" / "v1-archive"
if str(_ARCHIVE) not in sys.path:
    sys.path.insert(0, str(_ARCHIVE))
