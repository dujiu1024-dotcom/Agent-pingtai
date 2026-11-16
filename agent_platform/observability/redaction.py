"""Bounded, recursive redaction for telemetry and diagnostic payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|access[_-]?token|refresh[_-]?token|auth[_-]?token|api[_-]?token|token[_-]?(?:value|secret)|api[_-]?key|authorization|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_API_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[0-9A-Za-z_-]{20,})\b")
_SECRET_ASSIGNMENT = re.compile(
    r"""(?i)(?<![A-Za-z0-9_])(["']?)([A-Za-z0-9_-]*(?:password|passwd|secret|access[_-]?token|refresh[_-]?token|auth[_-]?token|api[_-]?token|api[_-]?key|authorization|cookie|credential|private[_-]?key)[A-Za-z0-9_-]*)\1\s*([:=])\s*("[^"]*"|'[^']*'|[^\s,;}\]]+)"""
)


def _redact_assignment(match: re.Match[str]) -> str:
    key = f"{match.group(1)}{match.group(2)}{match.group(1)}"
    value = '"[REDACTED]"' if match.group(4).startswith(('"', "\'")) else "[REDACTED]"
    return f"{key}{match.group(3)}{value}"


def redact_text(value: str, *, max_chars: int = 2000) -> str:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    redacted = _API_KEY.sub("[REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT.sub(_redact_assignment, redacted)
    if len(redacted) > max_chars:
        return redacted[:max_chars] + "…[TRUNCATED]"
    return redacted


def redact_payload(value: Any, *, max_text_chars: int = 2000, _depth: int = 0) -> Any:
    """Redact secret-bearing keys and token-like strings without mutating input."""
    if _depth > 12:
        return "[MAX_DEPTH]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key)
            result[clean_key] = (
                "[REDACTED]" if _SENSITIVE_KEY.search(clean_key)
                else redact_payload(item, max_text_chars=max_text_chars, _depth=_depth + 1)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [redact_payload(item, max_text_chars=max_text_chars, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value, max_chars=max_text_chars)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value), max_chars=max_text_chars)
