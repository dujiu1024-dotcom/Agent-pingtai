"""Production composition factory for the platform HTTP API."""
from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlsplit

from fastapi import FastAPI

from agent_platform.api.identity import create_identity_router
from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.identity.http import (
    IdentityASGIMiddleware,
    RedisSlidingWindowRateLimiter,
)
from agent_platform.identity.service import IdentityService
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.extraction import MemoryExtractionService
from agent_platform.memory.service import MemoryService


def create_platform_app(
    *,
    identity: IdentityService,
    identity_repository: Any,
    knowledge: KnowledgeService,
    memory: MemoryService,
    redis: Any,
    allowed_origins: Sequence[str],
    service_identity: Any | None = None,
    rag: RAGService | None = None,
    memory_extraction: MemoryExtractionService | None = None,
) -> FastAPI:
    """Assemble auth middleware, identity routes, and tenant-aware business routes.

    A shared Redis limiter and an explicit HTTPS Origin allowlist are mandatory
    so multi-instance deployments do not silently fall back to process-local
    security controls.
    """
    if isinstance(allowed_origins, str):
        raise ValueError("allowed_origins must be a sequence of exact origins")
    origins = tuple(origin.strip() for origin in allowed_origins if origin.strip())
    if not origins:
        raise ValueError("at least one exact HTTPS allowed_origin is required")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in parsed.netloc
        ):
            raise ValueError("allowed_origins must contain exact HTTPS origins only")

    app = FastAPI()
    app.add_middleware(
        IdentityASGIMiddleware,
        identity=identity,
        service_identity=service_identity,
        allowed_origins=origins,
        protected_paths=("/api", "/auth"),
        login_rate_limiter=RedisSlidingWindowRateLimiter(redis),
    )
    app.include_router(create_identity_router(identity=identity))
    app.include_router(create_knowledge_memory_router(
        knowledge=knowledge,
        memory=memory,
        rag=rag,
        memory_extraction=memory_extraction,
        identity_repository=identity_repository,
    ))
    return app
