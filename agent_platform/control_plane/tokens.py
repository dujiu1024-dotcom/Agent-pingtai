"""Short-lived Run-bound opaque Gateway credentials; plaintext is returned once."""
from __future__ import annotations
import hashlib, secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

def utcnow() -> datetime: return datetime.now(timezone.utc)

@dataclass(frozen=True)
class RunTokenRecord:
    token_id: str; tenant_id: str; run_id: str; agent_id: str; actor_id: str
    endpoint_url: str; token_digest: str; scopes: tuple[str, ...]
    issued_at: datetime; expires_at: datetime; revoked_at: datetime | None = None

@dataclass(frozen=True)
class IssuedRunToken:
    token_id: str; token: str; endpoint_url: str; expires_at: datetime

class RunTokenStore(Protocol):
    async def insert(self, record: RunTokenRecord) -> None: ...
    async def get_by_digest(self, digest: str) -> RunTokenRecord | None: ...
    async def revoke(self, token_id: str, tenant_id: str, at: datetime) -> bool: ...
    async def revoke_run(self, tenant_id: str, run_id: str, at: datetime) -> int: ...
    async def delete_expired(self, before: datetime, limit: int = 500) -> int: ...

class InMemoryRunTokenStore:
    """Test-only store. Production deployments must use PostgresRunTokenStore."""
    def __init__(self) -> None:
        import asyncio
        self._lock=asyncio.Lock(); self._rows: dict[str,RunTokenRecord]={}
    async def insert(self, record: RunTokenRecord) -> None:
        async with self._lock:
            if record.token_digest in self._rows: raise ValueError("duplicate token digest")
            self._rows[record.token_digest]=record
    async def get_by_digest(self,digest: str) -> RunTokenRecord | None:
        async with self._lock: return self._rows.get(digest)
    async def revoke(self,token_id: str,tenant_id: str,at: datetime) -> bool:
        async with self._lock:
            for digest,row in tuple(self._rows.items()):
                if row.token_id==token_id and row.tenant_id==tenant_id and row.revoked_at is None:
                    self._rows[digest]=RunTokenRecord(**{**row.__dict__,"revoked_at":at}); return True
            return False
    async def revoke_run(self,tenant_id: str,run_id: str,at: datetime) -> int:
        count=0
        async with self._lock:
            for digest,row in tuple(self._rows.items()):
                if row.tenant_id==tenant_id and row.run_id==run_id and row.revoked_at is None:
                    self._rows[digest]=RunTokenRecord(**{**row.__dict__,"revoked_at":at}); count+=1
        return count
    async def delete_expired(self,before: datetime,limit: int=500) -> int:
        if limit<1: raise ValueError("limit must be positive")
        async with self._lock:
            keys=[k for k,v in self._rows.items() if v.expires_at<before][:limit]
            for key in keys: del self._rows[key]
            return len(keys)

class PostgresRunTokenStore:
    """PostgreSQL adapter using the same async connection-factory pattern as identity."""
    def __init__(self,connection_factory: Any) -> None: self._connect=connection_factory
    @classmethod
    def from_dsn(cls,dsn: str) -> "PostgresRunTokenStore":
        if not dsn: raise ValueError("PostgreSQL DSN is required")
        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc: raise RuntimeError("Install psycopg[binary,pool]") from exc
            return await AsyncConnection.connect(dsn,row_factory=dict_row)
        return cls(connect)
    @staticmethod
    def _record(row: dict[str,Any] | None) -> RunTokenRecord | None:
        if row is None: return None
        return RunTokenRecord(str(row["token_id"]),row["tenant_id"],str(row["run_id"]),
            row["agent_id"],row["actor_id"],row["endpoint_url"],row["token_digest"],
            tuple(row["scopes"]),row["issued_at"],row["expires_at"],row["revoked_at"])
    async def insert(self,r: RunTokenRecord) -> None:
        c=await self._connect()
        async with c:
            cur=await c.execute("""INSERT INTO platform_run_tokens
              (token_id,tenant_id,run_id,agent_id,actor_id,endpoint_url,token_digest,scopes,issued_at,expires_at)
              SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s WHERE EXISTS
              (SELECT 1 FROM platform_runs WHERE tenant_id=%s AND run_id=%s AND agent_id=%s
               AND status IN ('queued','running','paused')) RETURNING token_id""",
              (r.token_id,r.tenant_id,r.run_id,r.agent_id,r.actor_id,r.endpoint_url,r.token_digest,
               list(r.scopes),r.issued_at,r.expires_at,r.tenant_id,r.run_id,r.agent_id))
            if await cur.fetchone() is None: raise PermissionError("active tenant Run required")
    async def get_by_digest(self,digest: str) -> RunTokenRecord | None:
        c=await self._connect()
        async with c:
            cur=await c.execute("""SELECT token_id,tenant_id,run_id,agent_id,actor_id,endpoint_url,
              token_digest,scopes,issued_at,expires_at,revoked_at FROM platform_run_tokens WHERE token_digest=%s""",(digest,))
            return self._record(await cur.fetchone())
    async def revoke(self,token_id: str,tenant_id: str,at: datetime) -> bool:
        c=await self._connect()
        async with c:
            cur=await c.execute("""UPDATE platform_run_tokens SET revoked_at=%s
              WHERE token_id=%s AND tenant_id=%s AND revoked_at IS NULL RETURNING token_id""",(at,token_id,tenant_id))
            return await cur.fetchone() is not None
    async def revoke_run(self,tenant_id: str,run_id: str,at: datetime) -> int:
        c=await self._connect()
        async with c:
            cur=await c.execute("""UPDATE platform_run_tokens SET revoked_at=%s
              WHERE tenant_id=%s AND run_id=%s AND revoked_at IS NULL RETURNING token_id""",(at,tenant_id,run_id))
            return len(await cur.fetchall())
    async def delete_expired(self,before: datetime,limit: int=500) -> int:
        if limit<1: raise ValueError("limit must be positive")
        c=await self._connect()
        async with c:
            cur=await c.execute("""WITH expired AS (SELECT token_id FROM platform_run_tokens
              WHERE expires_at<%s ORDER BY expires_at FOR UPDATE SKIP LOCKED LIMIT %s)
              DELETE FROM platform_run_tokens t USING expired e WHERE t.token_id=e.token_id RETURNING t.token_id""",(before,limit))
            return len(await cur.fetchall())

class RunTokenService:
    def __init__(self,store: RunTokenStore,*,max_ttl: timedelta=timedelta(minutes=15)) -> None:
        if max_ttl<=timedelta(0): raise ValueError("max_ttl must be positive")
        self.store=store; self.max_ttl=max_ttl
    async def issue(self,*,tenant_id: str,run_id: str,agent_id: str,actor_id: str,
      endpoint_url: str,ttl: timedelta=timedelta(minutes=5),scopes: tuple[str,...]=("platform:tools",)) -> IssuedRunToken:
        for value,name in ((tenant_id,"tenant_id"),(run_id,"run_id"),(agent_id,"agent_id"),(actor_id,"actor_id")):
            if not value.strip(): raise ValueError(f"{name} must not be empty")
        p=urlsplit(endpoint_url); loop=p.hostname in {"127.0.0.1","localhost","::1"}
        if (p.scheme!="https" and not (p.scheme=="http" and loop)) or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError("Gateway endpoint must be HTTPS (or loopback HTTP) without userinfo/query/fragment")
        if ttl<=timedelta(0) or ttl>self.max_ttl: raise ValueError("token TTL outside configured bounds")
        if not scopes or any(not s.strip() for s in scopes): raise ValueError("non-empty scopes required")
        token=secrets.token_urlsafe(32); now=utcnow(); expires=now+ttl
        record=RunTokenRecord(str(uuid4()),tenant_id,run_id,agent_id,actor_id,endpoint_url,
          hashlib.sha256(token.encode()).hexdigest(),tuple(sorted(set(scopes))),now,expires)
        await self.store.insert(record)
        return IssuedRunToken(record.token_id,token,endpoint_url,expires)
    async def resolve(self,token: str,*,endpoint_url: str | None=None) -> RunTokenRecord | None:
        if not token: return None
        row=await self.store.get_by_digest(hashlib.sha256(token.encode()).hexdigest())
        if row is None or row.revoked_at is not None or utcnow()>=row.expires_at: return None
        if endpoint_url is not None and not secrets.compare_digest(row.endpoint_url,endpoint_url): return None
        return row
    async def revoke(self,*,tenant_id: str,token_id: str) -> bool:
        return await self.store.revoke(token_id,tenant_id,utcnow())
    async def revoke_run(self,*,tenant_id: str,run_id: str) -> int:
        return await self.store.revoke_run(tenant_id,run_id,utcnow())
    async def cleanup_expired(self,*,limit: int=500) -> int:
        return await self.store.delete_expired(utcnow(),limit)
