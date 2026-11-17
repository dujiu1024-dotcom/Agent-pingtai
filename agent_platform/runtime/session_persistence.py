"""Durable conversation history integration for Run execution.

LangGraph's PostgreSQL checkpointer remains the source of truth for graph node
state. This adapter mirrors the checkpoint reference into the tenant-scoped
session snapshot table and makes prior conversation messages available to the
graph through configurable.platform_session_history.
"""
from __future__ import annotations

import json
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5

from agent_platform.control_plane.sessions_assets import (
    ContextWindowSnapshot,
    ConversationMessage,
    MessageRole,
    MessageTokenUsage,
)


class SessionRunPersistence:
    """Load and append session messages around a durable LangGraph Run."""

    def __init__(self, repository: Any, *, history_limit: int = 500) -> None:
        if not 1 <= history_limit <= 500:
            raise ValueError("history_limit must be between 1 and 500")
        self.repository = repository
        self.history_limit = history_limit

    @staticmethod
    def _user_content(payload: Any) -> str | None:
        if not isinstance(payload, Mapping):
            return payload if isinstance(payload, str) and payload.strip() else None
        for key in ("message", "prompt", "input", "task"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        messages = payload.get("messages")
        if isinstance(messages, list):
            for item in reversed(messages):
                if isinstance(item, Mapping) and item.get("role") in {"user", "human"}:
                    value = item.get("content")
                    if isinstance(value, str) and value.strip():
                        return value
        return None

    @staticmethod
    def _message_view(message: ConversationMessage) -> dict[str, Any]:
        return {
            "id": message.message_id,
            "role": message.role.value,
            "content": message.content,
            "created_at": message.created_at.isoformat(),
            "token_usage": ({
                "prompt_tokens": message.token_usage.prompt_tokens,
                "completion_tokens": message.token_usage.completion_tokens,
                "total_tokens": message.token_usage.total_tokens,
                "model": message.token_usage.model,
            } if message.token_usage else None),
            "metadata": dict(message.metadata),
        }

    async def begin_run(
        self, *, tenant_id: str, session_id: str, run_id: str,
        actor_id: str, payload: Any,
    ) -> tuple[dict[str, Any], ...]:
        session = await self.repository.get_session(tenant_id, session_id)
        if session is None or session.status != "active":
            raise LookupError("session not found or inactive")
        rows = await self.repository.list_messages(
            tenant_id, session_id, limit=self.history_limit,
        )
        content = self._user_content(payload)
        if content:
            # Stable identity makes worker redelivery and interrupted Run recovery idempotent.
            message_id = str(uuid5(NAMESPACE_URL, f"session-run:{tenant_id}:{session_id}:{run_id}:user"))
            existing = next((item for item in rows if item.message_id == message_id), None)
            if existing is None:
                await self.repository.append_message(ConversationMessage(
                    tenant_id=tenant_id, message_id=message_id, session_id=session_id,
                    role=MessageRole.USER, content=content, actor_id=actor_id,
                    run_id=run_id, metadata={"source": "run_input"},
                ))
                rows = await self.repository.list_messages(
                    tenant_id, session_id, limit=self.history_limit,
                )
            elif existing.content != content or existing.actor_id != actor_id:
                raise ValueError("Run input conflicts with its persisted session message")
        return tuple(self._message_view(item) for item in rows)

    async def append_progress(
        self, *, tenant_id: str, session_id: str, run_id: str,
        actor_id: str, state: Mapping[str, Any],
    ) -> int:
        """Append newly emitted LangGraph messages after each persisted graph update."""
        raw_messages = state.get("messages", ())
        if not isinstance(raw_messages, (list, tuple)):
            return 0
        rows = await self.repository.list_messages(
            tenant_id, session_id, limit=self.history_limit,
        )
        existing_ids = {item.message_id for item in rows}
        appended = 0
        for index, item in enumerate(raw_messages):
            if isinstance(item, Mapping):
                role_value = item.get("role") or item.get("type")
                content = item.get("content")
                source_id = item.get("id")
                usage_data = item.get("usage_metadata")
            else:
                role_value = getattr(item, "type", None) or getattr(item, "role", None)
                content = getattr(item, "content", None)
                source_id = getattr(item, "id", None)
                usage_data = getattr(item, "usage_metadata", None)
            role_aliases = {
                "human": MessageRole.USER, "user": MessageRole.USER,
                "ai": MessageRole.ASSISTANT, "assistant": MessageRole.ASSISTANT,
                "system": MessageRole.SYSTEM, "tool": MessageRole.TOOL,
                "function": MessageRole.TOOL, "agent": MessageRole.AGENT,
            }
            role = role_aliases.get(str(role_value).lower())
            if role is None or not isinstance(content, str) or not content.strip():
                continue
            identity = str(source_id) if source_id else f"{index}:{role.value}:{content}"
            message_id = str(uuid5(
                NAMESPACE_URL, f"session-run:{tenant_id}:{session_id}:{run_id}:stream:{identity}",
            ))
            if message_id in existing_ids:
                continue
            usage = None
            if isinstance(usage_data, Mapping):
                prompt = usage_data.get("input_tokens", usage_data.get("prompt_tokens", 0))
                completion = usage_data.get("output_tokens", usage_data.get("completion_tokens", 0))
                total = usage_data.get("total_tokens", prompt + completion if type(prompt) is int and type(completion) is int else -1)
                if all(type(value) is int and value >= 0 for value in (prompt, completion, total)) and total == prompt + completion:
                    usage = MessageTokenUsage(
                        prompt_tokens=prompt, completion_tokens=completion,
                        total_tokens=total, model=usage_data.get("model"),
                    )
            await self.repository.append_message(ConversationMessage(
                tenant_id=tenant_id, message_id=message_id, session_id=session_id,
                role=role, content=content, actor_id=actor_id, run_id=run_id,
                token_usage=usage, metadata={"source": "run_stream", "sequence": index},
            ))
            existing_ids.add(message_id)
            appended += 1
        return appended

    async def append_assistant(
        self, *, tenant_id: str, session_id: str, run_id: str,
        actor_id: str, result: Any,
    ) -> ConversationMessage | None:
        if result is None:
            return None
        content = result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False, sort_keys=True, default=str,
        )
        message_id = str(uuid5(NAMESPACE_URL, f"session-run:{tenant_id}:{session_id}:{run_id}:assistant"))
        rows = await self.repository.list_messages(
            tenant_id, session_id, limit=self.history_limit,
        )
        existing = next((
            item for item in rows
            if item.run_id == run_id and item.role == MessageRole.ASSISTANT
            and item.content == content
        ), None)
        if existing is not None:
            return existing
        usage_data = result.get("usage") if isinstance(result, Mapping) else None
        usage = None
        if isinstance(usage_data, Mapping):
            prompt = usage_data.get("prompt_tokens", 0)
            completion = usage_data.get("completion_tokens", 0)
            total = usage_data.get("total_tokens", prompt + completion)
            if all(type(value) is int and value >= 0 for value in (prompt, completion, total)) and total == prompt + completion:
                usage = MessageTokenUsage(
                    prompt_tokens=prompt, completion_tokens=completion,
                    total_tokens=total, model=usage_data.get("model"),
                )
        message = ConversationMessage(
            tenant_id=tenant_id, message_id=message_id, session_id=session_id,
            role=MessageRole.ASSISTANT, content=content, actor_id=actor_id,
            run_id=run_id, token_usage=usage, metadata={"source": "run_result"},
        )
        await self.repository.append_message(message)
        return message

    async def checkpoint(
        self, *, graph: Any, config: Mapping[str, Any], tenant_id: str,
        session_id: str, run_id: str, actor_id: str, token_count: int = 0,
        max_tokens: int = 2147483647,
    ) -> ContextWindowSnapshot | None:
        getter = getattr(graph, "aget_state", None)
        if not callable(getter):
            return None
        state = await getter(dict(config))
        configurable = (getattr(state, "config", None) or {}).get("configurable", {})
        checkpoint_id = configurable.get("checkpoint_id")
        if not checkpoint_id:
            return None
        values = getattr(state, "values", {})
        if not isinstance(values, Mapping):
            values = {"state": str(values)}
        payload = {
            "checkpoint_id": str(checkpoint_id),
            "values": dict(values),
            "next": list(getattr(state, "next", ()) or ()),
            "metadata": dict(getattr(state, "metadata", {}) or {}),
        }
        previous = await self.repository.list_context_snapshots(
            tenant_id, session_id, limit=1,
        )
        version = previous[0].version + 1 if previous else 1
        messages = await self.repository.list_messages(
            tenant_id, session_id, limit=1,
        )
        snapshot = ContextWindowSnapshot(
            tenant_id=tenant_id, session_id=session_id, snapshot_id=str(uuid4()),
            version=version, snapshot=payload, token_count=token_count,
            max_tokens=max_tokens, created_by=actor_id, run_id=run_id,
            last_message_id=messages[-1].message_id if messages else None,
        )
        await self.repository.create_context_snapshot(snapshot)
        return snapshot
