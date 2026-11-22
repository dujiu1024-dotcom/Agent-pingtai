"""PostgreSQL persistence for versioned, tenant-scoped memories."""
from __future__ import annotations

from typing import Any, Callable, Sequence

from agent_platform.memory.service import MemoryCandidate, MemoryRecord, MemoryScope, MemoryStatus

ConnectionFactory=Callable[[],Any]
def _jsonb(value: Any)->Any:
    try: from psycopg.types.json import Jsonb  # noqa: I001
    except ImportError as exc: raise RuntimeError("Install psycopg[binary,pool]") from exc
    return Jsonb(value)
class PostgresMemoryRepository:
    def __init__(self,connection_factory: ConnectionFactory)->None:self._connect=connection_factory
    @staticmethod
    def _record(row: dict[str,Any])->MemoryRecord:
        c=MemoryCandidate(tenant_id=row["tenant_id"],memory_id=str(row["memory_id"]),scope=MemoryScope(row["scope"]),scope_id=row["scope_id"],content=row["content"],source_ref=row["source_ref"],confidence=float(row["confidence"]),created_by=row["created_by"],expires_at=row["expires_at"],metadata=dict(row["metadata"] or {}),conflict_key=row.get("conflict_key"))
        return MemoryRecord(candidate=c,status=MemoryStatus(row["status"]),version=row["version"],confirmed_by=row["confirmed_by"],created_at=row["created_at"],updated_at=row["updated_at"],deleted_at=row["deleted_at"])
    async def _persist_version(self,conn: Any,record: MemoryRecord)->None:
        c=record.candidate
        await conn.execute("""INSERT INTO platform_memory_versions(tenant_id,memory_id,version,scope,scope_id,content,source_ref,confidence,conflict_key,status,created_by,confirmed_by,expires_at,metadata,recorded_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",(c.tenant_id,c.memory_id,record.version,c.scope.value,c.scope_id,c.content,c.source_ref,c.confidence,c.conflict_key,record.status.value,c.created_by,record.confirmed_by,c.expires_at,_jsonb(c.metadata),record.updated_at))
    async def put(self,record: MemoryRecord)->None:
        c=record.candidate; conn=await self._connect()
        async with conn:
            async with conn.transaction():
                if record.version==1:
                    await conn.execute("""INSERT INTO platform_memories(tenant_id,memory_id,scope,scope_id,content,source_ref,confidence,status,version,created_by,confirmed_by,expires_at,metadata,conflict_key,created_at,updated_at,deleted_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s)""",(c.tenant_id,c.memory_id,c.scope.value,c.scope_id,c.content,c.source_ref,c.confidence,record.status.value,c.created_by,record.confirmed_by,c.expires_at,_jsonb(c.metadata),c.conflict_key,record.created_at,record.updated_at,record.deleted_at))
                    await self._persist_version(conn,record)
                else:
                    cur=await conn.execute("""UPDATE platform_memories SET content=%s,source_ref=%s,confidence=%s,conflict_key=%s,status=%s,version=%s,confirmed_by=%s,updated_at=%s,deleted_at=%s WHERE tenant_id=%s AND memory_id=%s AND version=%s AND deleted_at IS NULL""",(c.content,c.source_ref,c.confidence,c.conflict_key,record.status.value,record.version,record.confirmed_by,record.updated_at,record.deleted_at,c.tenant_id,c.memory_id,record.version-1))
                    if cur.rowcount!=1:raise RuntimeError("memory version conflict")
                    await self._persist_version(conn,record)
    async def get(self,tenant_id: str,memory_id: str)->MemoryRecord|None:
        conn=await self._connect()
        async with conn:
            cur=await conn.execute("SELECT * FROM platform_memories WHERE tenant_id=%s AND memory_id=%s",(tenant_id,memory_id)); row=await cur.fetchone()
        return self._record(row) if row else None
    async def list(self,tenant_id: str,limit: int=1000)->Sequence[MemoryRecord]:
        if limit<1 or limit>5000:raise ValueError("limit must be between 1 and 5000")
        conn=await self._connect()
        async with conn:
            cur=await conn.execute("SELECT * FROM platform_memories WHERE tenant_id=%s ORDER BY updated_at DESC LIMIT %s",(tenant_id,limit)); rows=await cur.fetchall()
        return [self._record(r) for r in rows]
    async def list_versions(self,tenant_id: str,memory_id: str)->Sequence[MemoryRecord]:
        conn=await self._connect()
        async with conn:
            cur=await conn.execute("SELECT * FROM platform_memory_versions WHERE tenant_id=%s AND memory_id=%s ORDER BY version ASC",(tenant_id,memory_id))
            rows=await cur.fetchall()
        return [self._record({**row,"created_at":row["recorded_at"],"updated_at":row["recorded_at"],"deleted_at":None}) for row in rows]
    async def delete(self,tenant_id: str,memory_id: str,at)->bool:
        conn=await self._connect()
        async with conn:
            async with conn.transaction():
                cur=await conn.execute("UPDATE platform_memories SET status='deleted',deleted_at=%s,updated_at=%s,version=version+1 WHERE tenant_id=%s AND memory_id=%s AND deleted_at IS NULL RETURNING *",(at,at,tenant_id,memory_id))
                row=await cur.fetchone()
                if row is None:
                    cur=await conn.execute(
                        "SELECT 1 FROM platform_memories WHERE tenant_id=%s AND memory_id=%s AND deleted_at IS NOT NULL",
                        (tenant_id,memory_id),
                    )
                    if await cur.fetchone() is None:
                        return False
                else:
                    await self._persist_version(conn,self._record(row))
                # Tombstone/version and cleanup intent commit atomically.
                await conn.execute(
                    """INSERT INTO platform_memory_deletion_jobs
                       (tenant_id,memory_id,status)
                       VALUES (%s,%s,'pending')
                       ON CONFLICT (tenant_id,memory_id) DO UPDATE
                       SET status='retry', attempts=0, next_attempt_at=now(),
                           last_error_code=NULL, updated_at=now()
                       WHERE platform_memory_deletion_jobs.status='dead'""",
                    (tenant_id,memory_id),
                )
                return row is not None
