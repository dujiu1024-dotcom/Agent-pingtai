"""Context-local token callback used by Run-scoped model clients."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

TokenCallback = Callable[[str], Awaitable[None]]
_callback: ContextVar[TokenCallback | None] = ContextVar("platform_stream_token_callback", default=None)


@contextmanager
def token_callback_context(callback: TokenCallback | None) -> Iterator[None]:
    token: Token[TokenCallback | None] = _callback.set(callback)
    try:
        yield
    finally:
        _callback.reset(token)


def current_token_callback() -> TokenCallback | None:
    return _callback.get()
