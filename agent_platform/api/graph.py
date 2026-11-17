"""Authenticated HTTP routes for bounded graph and hybrid retrieval."""
from __future__ import annotations

from typing import Awaitable, Callable, Mapping, Any

from fastapi import APIRouter, HTTPException, Request

from agent_platform.graph.query import (
    GraphQueryAuthorizationError,
    GraphQueryError,
    GraphQueryService,
    GraphQuerySpec,
    GraphQueryTimeout,
)
from agent_platform.knowledge.contracts import KnowledgePrincipal

PrincipalResolver = Callable[[Request], Awaitable[KnowledgePrincipal]]
QueryAuthorizer = Callable[[KnowledgePrincipal], Awaitable[bool]]


def create_graph_query_router(
    *,
    service: GraphQueryService,
    resolve_principal: PrincipalResolver,
    authorize_query: QueryAuthorizer,
) -> APIRouter:
    """Build graph endpoints; host supplies authenticated principal and ACL policy."""
    if not callable(resolve_principal) or not callable(authorize_query):
        raise ValueError("principal resolver and graph query authorizer are required")
    router = APIRouter(prefix="/api/platform/v1/graph", tags=["knowledge-graph"])

    async def principal_for(request: Request) -> KnowledgePrincipal:
        principal = await resolve_principal(request)
        if not isinstance(principal, KnowledgePrincipal):
            raise HTTPException(401, "authenticated knowledge principal required")
        if not await authorize_query(principal):
            raise HTTPException(403, "graph query access denied")
        return principal

    @router.post("/query")
    async def graph_query(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
            spec = GraphQuerySpec.from_payload(payload)
            principal = await principal_for(request)
            result = await service.query(principal=principal, spec=spec)
        except GraphQueryAuthorizationError as exc:
            raise HTTPException(403, str(exc)) from exc
        except GraphQueryTimeout as exc:
            raise HTTPException(504, str(exc)) from exc
        except GraphQueryError as exc:
            raise HTTPException(422, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, "invalid graph query request") from exc
        return {
            "items": [dict(item) for item in result.items],
            "truncated": result.truncated,
            "elapsed_ms": result.elapsed_ms,
        }

    @router.post("/hybrid")
    async def hybrid_query(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
            if not isinstance(payload, Mapping) or set(payload) != {"query", "graph"}:
                raise GraphQueryError("hybrid request must contain only query and graph")
            query = payload.get("query")
            spec = GraphQuerySpec.from_payload(payload.get("graph"))
            principal = await principal_for(request)
            result = await service.hybrid_search(
                principal=principal, query=query, spec=spec,
            )
        except GraphQueryAuthorizationError as exc:
            raise HTTPException(403, str(exc)) from exc
        except GraphQueryTimeout as exc:
            raise HTTPException(504, str(exc)) from exc
        except GraphQueryError as exc:
            raise HTTPException(422, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, "invalid hybrid graph query request") from exc
        return {
            "items": [dict(item) for item in result.items],
            "truncated": result.truncated,
            "elapsed_ms": result.elapsed_ms,
        }

    return router
