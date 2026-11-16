"""Lifecycle-managed PostgreSQL Checkpointer construction for LangGraph hosts.

The context manager owns the saver connection for as long as the compiled graph
is in use. Hosts must keep the context open for the worker/application lifespan.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any


@asynccontextmanager
async def postgres_checkpointer(
    dsn: str, *, run_setup: bool = True,
) -> AsyncIterator[Any]:
    """Yield a connected AsyncPostgresSaver and optionally install its schema."""
    if not isinstance(dsn, str) or not dsn.strip():
        raise ValueError("PostgreSQL DSN is required")
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL LangGraph checkpointing requires langgraph-checkpoint-postgres"
        ) from exc

    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        if run_setup:
            await saver.setup()
        yield saver


@asynccontextmanager
async def compiled_postgres_graph(
    builder: Any, dsn: str, *, run_setup: bool = True,
) -> AsyncIterator[Any]:
    """Compile and yield a graph while retaining its PostgreSQL saver connection."""
    async with postgres_checkpointer(dsn, run_setup=run_setup) as saver:
        yield builder.compile(checkpointer=saver)
