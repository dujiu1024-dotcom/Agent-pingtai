"""Shared PostgreSQL endpoint directory with renewable owner leases."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from typing import Any
from agent_platform.gateway.service import EndpointDirectory,EndpointRecord

class PostgresEndpointDirectory(EndpointDirectory):
 def __init__(self,connection_factory:Any)->None:self._connect=connection_factory
 @classmethod
 def from_dsn(cls,dsn:str)->"PostgresEndpointDirectory":
  if not dsn:raise ValueError("PostgreSQL DSN required")
  async def connect()->Any:
   try:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
   except ImportError as exc:raise RuntimeError("Install psycopg[binary,pool]") from exc
   return await AsyncConnection.connect(dsn,row_factory=dict_row)
  return cls(connect)
 @staticmethod
 def _record(row:dict[str,Any]|None)->EndpointRecord|None:
  if row is None:return None
  return EndpointRecord(row["tenant_id"],str(row["run_id"]),row["instance_id"],row["endpoint_url"],row["status"],row["created_at"],row.get("lease_expires_at"))
 async def register(self,r:EndpointRecord)->None:
  lease=r.lease_expires_at or datetime.now(timezone.utc)+timedelta(seconds=30)
  c=await self._connect()
  async with c:
   cur=await c.execute("""INSERT INTO platform_gateway_endpoints
    (tenant_id,run_id,instance_id,endpoint_url,status,created_at,updated_at,lease_expires_at)
    VALUES(%s,%s,%s,%s,'active',%s,now(),%s)
    ON CONFLICT(tenant_id,run_id) DO UPDATE SET instance_id=EXCLUDED.instance_id,endpoint_url=EXCLUDED.endpoint_url,
    status='active',created_at=EXCLUDED.created_at,updated_at=now(),lease_expires_at=EXCLUDED.lease_expires_at
    WHERE platform_gateway_endpoints.status='inactive' OR platform_gateway_endpoints.lease_expires_at IS NULL OR platform_gateway_endpoints.lease_expires_at<=now()
    RETURNING run_id""",(r.tenant_id,r.run_id,r.instance_id,r.endpoint_url,r.created_at,lease))
   if await cur.fetchone() is None:raise ValueError("active endpoint lease already exists")
 async def get(self,tenant_id:str,run_id:str)->EndpointRecord|None:
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT tenant_id,run_id,instance_id,endpoint_url,status,created_at,lease_expires_at
    FROM platform_gateway_endpoints WHERE tenant_id=%s AND run_id=%s""",(tenant_id,run_id))
   return self._record(await cur.fetchone())
 async def heartbeat(self,tenant_id:str,run_id:str,instance_id:str,*,lease_seconds:int)->bool:
  if lease_seconds<5:raise ValueError("lease_seconds must be at least five")
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_gateway_endpoints SET lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
    WHERE tenant_id=%s AND run_id=%s AND instance_id=%s AND status='active' AND (lease_expires_at IS NULL OR lease_expires_at>now())
    RETURNING run_id""",(lease_seconds,tenant_id,run_id,instance_id))
   return await cur.fetchone() is not None
 async def deactivate(self,tenant_id:str,run_id:str,instance_id:str|None=None)->bool:
  c=await self._connect()
  async with c:
   if instance_id is None:
    cur=await c.execute("""UPDATE platform_gateway_endpoints SET status='inactive',updated_at=now()
     WHERE tenant_id=%s AND run_id=%s AND status='active' RETURNING run_id""",(tenant_id,run_id))
   else:
    cur=await c.execute("""UPDATE platform_gateway_endpoints SET status='inactive',updated_at=now()
     WHERE tenant_id=%s AND run_id=%s AND instance_id=%s AND status='active' RETURNING run_id""",(tenant_id,run_id,instance_id))
   return await cur.fetchone() is not None
