"""Redis Streams transport adapter; PostgreSQL remains the source of truth."""

from __future__ import annotations

import json
from typing import Any, Mapping


class RedisStreamsTransport:
    def __init__(self, url: str, *, stream_prefix: str = "agent-platform") -> None:
        if not url:
            raise ValueError("Redis URL is required")
        self.url = url
        self.stream_prefix = stream_prefix
        self._client: Any = None

    async def connect(self) -> None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError("Install redis>=5 to use Redis Streams transport") from exc
        self._client = Redis.from_url(self.url, decode_responses=True)

    def _stream(self, tenant_id: str, topic: str) -> str:
        if not tenant_id or not topic:
            raise ValueError("tenant_id and topic are required")
        return f"{self.stream_prefix}:{tenant_id}:{topic}"

    def _dead_letter_stream(self, tenant_id: str, topic: str) -> str:
        return f"{self.stream_prefix}:{tenant_id}:{topic}:dead-letter"

    async def publish(
        self, *, tenant_id: str, topic: str, event_id: str,
        payload: Mapping[str, Any],
    ) -> str:
        if self._client is None:
            raise RuntimeError("RedisStreamsTransport.connect() must be called first")
        if not event_id:
            raise ValueError("event_id is required for idempotent consumption")
        fields = {
            "event_id": event_id,
            "payload": json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")),
        }
        # Never trim on publish: XADD MAXLEN can remove payloads for
        # unacknowledged Pending Entries List (PEL) records.
        message_id = await self._client.xadd(
            self._stream(tenant_id, topic), fields,
        )
        return str(message_id)

    async def ensure_group(
        self, *, tenant_id: str, topic: str, group: str, start_id: str = "0"
    ) -> None:
        if self._client is None:
            raise RuntimeError("RedisStreamsTransport.connect() must be called first")
        try:
            await self._client.xgroup_create(
                self._stream(tenant_id, topic), group, id=start_id, mkstream=True
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_group(
        self, *, tenant_id: str, topic: str, group: str, consumer: str,
        count: int = 20, block_ms: int = 5_000,
    ) -> list[tuple[str, dict[str, str]]]:
        if self._client is None:
            raise RuntimeError("RedisStreamsTransport.connect() must be called first")
        rows = await self._client.xreadgroup(
            group, consumer, {self._stream(tenant_id, topic): ">"}, count=count, block=block_ms,
        )
        return [
            (message_id, fields)
            for _stream_name, messages in rows
            for message_id, fields in messages
        ]

    async def claim_pending(
        self, *, tenant_id: str, topic: str, group: str, consumer: str,
        min_idle_ms: int = 30_000, start_id: str = "0-0", count: int = 20,
    ) -> list[tuple[str, dict[str, str]]]:
        if self._client is None:
            raise RuntimeError("RedisStreamsTransport.connect() must be called first")
        if min_idle_ms < 0 or count < 1 or count > 500:
            raise ValueError("min_idle_ms must be >= 0 and count must be between 1 and 500")
        result = await self._client.xautoclaim(
            self._stream(tenant_id, topic), group, consumer,
            min_idle_time=min_idle_ms, start_id=start_id, count=count,
        )
        messages = result[1] if len(result) > 1 else []
        return [(message_id, fields) for message_id, fields in messages]

    async def acknowledge(
        self, *, tenant_id: str, topic: str, group: str, message_id: str
    ) -> int:
        if self._client is None:
            raise RuntimeError("RedisStreamsTransport.connect() must be called first")
        return int(await self._client.xack(self._stream(tenant_id, topic), group, message_id))

    async def dead_letter(
        self, *, tenant_id: str, topic: str, group: str, message_id: str,
        fields: Mapping[str, str], event_id: str, reason: str, consumer_name: str,
    ) -> str:
        """Atomically copy the poison message to the DLQ and ACK the source entry."""
        if self._client is None:
            raise RuntimeError("RedisStreamsTransport.connect() must be called first")
        source = self._stream(tenant_id, topic)
        destination = self._dead_letter_stream(tenant_id, topic)
        dlq_fields = {
            **dict(fields), "event_id": event_id, "failure_reason": reason,
            "source_stream": source, "source_message_id": message_id,
            "consumer_name": consumer_name,
        }
        async with self._client.pipeline(transaction=True) as pipe:
            # DLQ entries are retained until every configured DLQ consumer group
            # acknowledges them and explicit safe trimming is requested.
            pipe.xadd(destination, dlq_fields)
            pipe.xack(source, group, message_id)
            results = await pipe.execute()
        return str(results[0])


    @staticmethod
    def _stream_id_key(message_id: str) -> tuple[int, int]:
        major, minor = message_id.split("-", 1)
        return int(major), int(minor)

    async def trim_acknowledged(
        self, *, tenant_id: str, topic: str, dead_letter: bool = False,
    ) -> int:
        """Trim only entries known to be acknowledged by every consumer group.

        The safe cutoff is the earliest pending ID or, when a group has no
        pending entries, that group's last-delivered ID. Entries at/after the
        cutoff are retained. No groups means no trimming.
        """
        if self._client is None:
            raise RuntimeError("RedisStreamsTransport.connect() must be called first")
        stream = (
            self._dead_letter_stream(tenant_id, topic)
            if dead_letter else self._stream(tenant_id, topic)
        )
        groups = await self._client.xinfo_groups(stream)
        if not groups:
            return 0

        cutoffs: list[str] = []
        for group in groups:
            group_name = group.get("name")
            if not group_name:
                return 0
            pending = await self._client.xpending(stream, group_name)
            if int(pending.get("pending", 0)) > 0:
                cutoff = pending.get("min")
            else:
                cutoff = group.get("last-delivered-id")
            if not isinstance(cutoff, str) or cutoff == "0-0":
                return 0
            cutoffs.append(cutoff)

        safe_cutoff = min(cutoffs, key=self._stream_id_key)
        return int(await self._client.xtrim(
            stream, minid=safe_cutoff, approximate=False,
        ))

    async def pending_summary(
        self, *, tenant_id: str, topic: str, group: str
    ) -> dict[str, Any]:
        """Return operational counters for dashboards and health checks."""
        if self._client is None:
            raise RuntimeError("RedisStreamsTransport.connect() must be called first")
        source = self._stream(tenant_id, topic)
        pending = await self._client.xpending(source, group)
        return {
            "pending": int(pending.get("pending", 0)),
            "oldest_pending_id": pending.get("min"),
            "newest_pending_id": pending.get("max"),
            "consumer_count": len(pending.get("consumers", [])),
            "dead_letter_count": int(await self._client.xlen(
                self._dead_letter_stream(tenant_id, topic)
            )),
        }

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
