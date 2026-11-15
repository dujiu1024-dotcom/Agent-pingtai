"""Thin adapter over LangGraph; workflow semantics stay in LangGraph itself."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any


def build_state_graph(
    *,
    state_schema: type,
    nodes: Mapping[str, Callable[..., Any]],
    edges: Sequence[tuple[str, str]] = (),
    conditional_edges: Sequence[tuple[str, Callable[..., str], Mapping[str, str]]] = (),
    entry_point: str | None = None,
    checkpointer: Any = None,
) -> Any:
    """Build and compile a LangGraph graph from registered platform nodes.

    LangGraph is an optional runtime dependency. Importing the platform package
    or running its domain tests does not require it.
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph is not installed. Install the platform runtime dependencies."
        ) from exc

    graph = StateGraph(state_schema)
    for name, node in nodes.items():
        graph.add_node(name, node)

    if entry_point:
        graph.add_edge(START, entry_point)
    for source, target in edges:
        graph.add_edge(source, END if target == "__end__" else target)
    for source, router, destinations in conditional_edges:
        normalized = {
            key: END if value == "__end__" else value
            for key, value in destinations.items()
        }
        graph.add_conditional_edges(source, router, normalized)

    return graph.compile(checkpointer=checkpointer)



@asynccontextmanager
async def postgres_checkpointer(dsn: str) -> AsyncIterator[Any]:
    """Yield a LangGraph PostgreSQL checkpointer with its schema initialized."""
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "Install langgraph-checkpoint-postgres to use durable workflow checkpoints"
        ) from exc

    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
