"""Repository-owned production ASGI composition root.

Import as `agent_platform.production:app`. Configuration is validated during
import so an incorrectly configured process never becomes a healthy API.
"""
from __future__ import annotations

from typing import Any

from agent_platform.api.app import create_platform_app
from agent_platform.identity.postgres import PostgresIdentityRepository
from agent_platform.identity.service import IdentityService
from agent_platform.knowledge.postgres_repository import PostgresKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.postgres_repository import PostgresMemoryRepository
from agent_platform.memory.service import MemoryService
from agent_platform.production_config import load_production_settings


settings = load_production_settings()


async def _postgres_connection() -> Any:
    try:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "production API requires psycopg[binary,pool] from requirements-platform.txt"
        ) from exc
    return await AsyncConnection.connect(settings.postgres_dsn, row_factory=dict_row)


try:
    from redis.asyncio import Redis
except ImportError as exc:
    raise RuntimeError(
        "production API requires redis>=5 from requirements-platform.txt"
    ) from exc

redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
identity_repository = PostgresIdentityRepository(_postgres_connection)
identity_service = IdentityService(identity_repository)
knowledge_service = KnowledgeService(PostgresKnowledgeRepository(_postgres_connection))
memory_service = MemoryService(PostgresMemoryRepository(_postgres_connection))


async def _postgres_ready() -> bool:
    connection = await _postgres_connection()
    async with connection:
        await connection.execute("SELECT 1")
    return True


app = create_platform_app(
    identity=identity_service,
    identity_repository=identity_repository,
    knowledge=knowledge_service,
    memory=memory_service,
    redis=redis_client,
    allowed_origins=settings.allowed_origins,
    readiness_checks={
        "postgres": _postgres_ready,
        "redis": redis_client.ping,
    },
)
app.state.production_settings = settings
app.state.identity_repository = identity_repository
app.state.knowledge_service = knowledge_service
app.state.memory_service = memory_service


async def _close_production_resources() -> None:
    await redis_client.aclose()


app.router.on_shutdown.append(_close_production_resources)
