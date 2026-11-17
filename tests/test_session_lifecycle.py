from datetime import datetime, timezone

import pytest

from agent_platform.control_plane.sessions_assets import (
    ConversationSession,
    InMemorySessionRepository,
    SessionError,
    SessionNotFound,
)


@pytest.mark.asyncio
async def test_session_lifecycle_archive_restore_delete_is_terminal():
    repository = InMemorySessionRepository()
    session = ConversationSession(
        tenant_id="tenant-a", session_id="session-a", owner_id="user-a",
    )
    await repository.create_session(session)

    archived = await repository.set_session_status("tenant-a", "session-a", "archived")
    assert archived.status == "archived"
    assert archived.updated_at >= session.updated_at

    restored = await repository.set_session_status("tenant-a", "session-a", "active")
    assert restored.status == "active"

    deleted = await repository.set_session_status("tenant-a", "session-a", "deleted")
    assert deleted.status == "deleted"
    assert await repository.get_session("tenant-a", "session-a") == deleted
    assert await repository.list_sessions("tenant-a", "user-a") == ()

    with pytest.raises(SessionNotFound):
        await repository.set_session_status("tenant-a", "session-a", "active")


@pytest.mark.asyncio
async def test_session_lifecycle_rejects_invalid_transitions_and_tenant_mismatch():
    repository = InMemorySessionRepository()
    await repository.create_session(ConversationSession(
        tenant_id="tenant-a", session_id="session-a", owner_id="user-a",
    ))

    with pytest.raises(SessionNotFound):
        await repository.set_session_status("tenant-b", "session-a", "archived")
    with pytest.raises(SessionError):
        await repository.set_session_status("tenant-a", "session-a", "active")
    with pytest.raises(SessionError):
        await repository.set_session_status("tenant-a", "session-a", "purged")
