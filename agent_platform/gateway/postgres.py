"""Shared PostgreSQL endpoint directory for multi-instance Gateway routing."""
from __future__ import annotations
from typing import Any
from agent_platform.gateway.service import EndpointDirectory, EndpointRecord
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
  return EndpointRecord(row["tenant_id"],str(row["run_id"]),row["instance_id"],row["endpoint_url"],row["status"],row["created_at"])
 async def register(self,r:EndpointRecord)->None:
  c=await self._connect()
  async with c:
   cur=await c.execute("""INSERT INTO platform_gateway_endpoints(tenant_id,run_id,instance_id,endpoint_url,status,created_at,updated_at)
    VALUES(%s,%s,%s,%s,'active',%s,now())
    ON CONFLICT(tenant_id,run_id) DO UPDATE SET instance_id=EXCLUDED.instance_id,endpoint_url=EXCLUDED.endpoint_url,
    status='active',created_at=EXCLUDED.created_at,updated_at=now()
    WHERE platform_gateway_endpoints.status='inactive' RETURNING run_id""",(r.tenant_id,r.run_id,r.instance_id,r.endpoint_url,r.created_at))
   if await cur.fetchone() is None:raise ValueError("active endpoint already registered for Run")
 async def get(self,tenant_id:str,run_id:str)->EndpointRecord|None:
  c=await self._connect()
  async with c:
   cur=await c.execute("SELECT tenant_id,run_id,instance_id,endpoint_url,status,created_at FROM platform_gateway_endpoints WHERE tenant_id=%s AND run_id=%s",(tenant_id,run_id))
   return self._record(await cur.fetchone())
 async def deactivate(self,tenant_id:str,run_id:str)->bool:
  c=await self._connect()
  async with c:
   cur=await c.execute("UPDATE platform_gateway_endpoints SET status='inactive',updated_at=now() WHERE tenant_id=%s AND run_id=%s AND status='active' RETURNING run_id",(tenant_id,run_id))
   return await cur.fetchone() is not None
