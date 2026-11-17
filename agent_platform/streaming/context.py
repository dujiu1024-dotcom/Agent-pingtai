"""Context-local callbacks used by Run-scoped model clients."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

TokenCallback = Callable[[str], Awaitable[None]]
RunEventCallback = Callable[..., Awaitable[None]]
_token_callback: ContextVar[TokenCallback | None] = ContextVar(
    "platform_stream_token_callback", default=None,
)
_run_event_callback: ContextVar[RunEventCallback | None] = ContextVar(
    "platform_run_event_callback", default=None,
)


@contextmanager
def token_callback_context(callback: TokenCallback | None) -> Iterator[None]:
    token: Token[TokenCallback | None] = _token_callback.set(callback)
    try:
        yield
    finally:
        _token_callback.reset(token)


@contextmanager
def run_event_context(callback: RunEventCallback | None) -> Iterator[None]:
    token: Token[RunEventCallback | None] = _run_event_callback.set(callback)
    try:
        yield
    finally:
        _run_event_callback.reset(token)


def current_token_callback() -> TokenCallback | None:
    return _token_callback.get()


def current_run_event_callback() -> RunEventCallback | None:
    return _run_event_callback.get()
