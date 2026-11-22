"""Trusted Workflow compiler resolver loading for production composition."""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Callable

from fastapi import HTTPException


def load_trusted_workflow_compiler_resolver() -> Callable[..., Any] | None:
    """Load the single deployment-installed Workflow compiler resolver.

    Executable node factories are deployment code, not Registry data. Only a
    reviewed Python distribution entry point may supply them; Agent-authored
    definitions and environment variables cannot select imports or callables.
    """
    candidates = tuple(
        entry for entry in entry_points(group="agent_platform.workflow_compiler")
        if entry.name == "resolver"
    )
    if len(candidates) > 1:
        raise RuntimeError(
            "multiple Workflow compiler resolvers are installed; exactly one is allowed"
        )
    if not candidates:
        return None
    resolver = candidates[0].load()
    if not callable(resolver):
        raise TypeError("trusted Workflow compiler entry point must load a callable resolver")
    return resolver


async def workflow_compiler_unavailable(_principal: Any) -> Any:
    """Keep production compile/publish routes mounted but fail closed until configured."""
    raise HTTPException(
        503,
        "trusted Workflow compiler resolver is not installed; compile and publish are disabled",
    )
