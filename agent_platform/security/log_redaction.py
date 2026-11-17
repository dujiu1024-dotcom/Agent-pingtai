"""Centralized redaction for credentials and secret material in application logs.

Register known secret values at the boundary where they enter the process. The
filter sanitizes both structured and %-formatted log records before handlers
format or export them. It intentionally never logs registry contents.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any

_REDACTED = "[REDACTED]"
_PATTERNS = (
    (re.compile(r"(?i)(\b(?:password|passwd|client_secret|secret_id|api[_-]?key|access[_-]?token|refresh[_-]?token|vault[_-]?token)\b\s*[:=]\s*)([^\s,;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"\b(?:hvs|s)\.[A-Za-z0-9_-]{12,}\b"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+(@)"), r"\1[REDACTED]\2"),
)


class SecretRedactor:
    """Thread-safe exact-value registry plus conservative credential patterns."""

    def __init__(self, secrets: Sequence[str] = ()) -> None:
        self._lock = threading.RLock()
        self._values: set[str] = set()
        for value in secrets:
            self.register(value)

    def register(self, value: str) -> None:
        if not isinstance(value, str) or len(value) < 1:
            raise ValueError("registered secrets must be strings of non-empty strings")
        with self._lock:
            self._values.add(value)

    def unregister(self, value: str) -> None:
        with self._lock:
            self._values.discard(value)

    def redact_text(self, value: str) -> str:
        with self._lock:
            secrets = sorted(self._values, key=len, reverse=True)
        for secret in secrets:
            value = value.replace(secret, _REDACTED)
        for pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
        return value

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, bytes):
            return self.redact_text(value.decode("utf-8", errors="replace"))
        if isinstance(value, Mapping):
            return {self.redact(key): self.redact(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, set):
            return {self.redact(item) for item in value}
        return value


class SecretRedactionFilter(logging.Filter):
    """Sanitize a LogRecord in place before any configured handler emits it."""

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or SecretRedactor()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = self.redactor.redact_text(rendered)
        record.args = ()
        if record.exc_info:
            try:
                record.exc_text = self.redactor.redact_text(
                    logging.Formatter().formatException(record.exc_info)
                )
            finally:
                record.exc_info = None
        if record.exc_text:
            record.exc_text = self.redactor.redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = self.redactor.redact_text(record.stack_info)
        for key, value in tuple(record.__dict__.items()):
            if key not in {"msg", "args", "exc_text", "stack_info"}:
                record.__dict__[key] = self.redactor.redact(value)
        return True


def configure_secret_log_redaction(
    logger: logging.Logger | None = None, *, redactor: SecretRedactor | None = None,
) -> SecretRedactionFilter:
    """Install one filter on the selected logger; call during process bootstrap."""
    target = logger or logging.getLogger()
    filter_instance = SecretRedactionFilter(redactor)
    target.addFilter(filter_instance)
    # Propagated records bypass ancestor logger filters; sanitize every output.
    for handler in target.handlers:
        handler.addFilter(filter_instance)
    return filter_instance
