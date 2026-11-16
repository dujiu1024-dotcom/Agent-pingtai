"""PostgreSQL Run state store with optimistic transitions and worker leases."""
from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from agent_platform.runtime.lifecycle import _ALLOWED, RunSnapshot, RunStateStore, RunStatus


class PostgresRunStateStore(RunStateStore):
 def __init__(self,connection_factory:Any)->None:self._connect=connection_factory
 @classmethod
 def from_dsn(cls,dsn:str)->"PostgresRunStateStore":
  if not dsn:raise ValueError("PostgreSQL DSN required")
  async def connect()->Any:
   try:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
   except ImportError as exc:raise RuntimeError("Install psycopg[binary,pool]") from exc
   return await AsyncConnection.connect(dsn,row_factory=dict_row)
  return cls(connect)
 @staticmethod
 def _snapshot(row:dict[str,Any]|None)->RunSnapshot|None:
  if row is None:return None
  return RunSnapshot(row["tenant_id"],str(row["run_id"]),RunStatus(row["status"]),int(row["version"]),row["checkpoint_ref"],row["last_error"],row["updated_at"])
 async def create_task_run(self, *, tenant_id:str, run_id:str, actor_id:str,
  agent_id:str, template_id:str, template_version:int, input:Mapping[str,Any],
  project_id:str|None=None, workflow_id:str|None=None,
  idempotency_key:str|None=None, request_digest:str|None=None)->bool:
  if not tenant_id.strip() or not actor_id.strip() or not agent_id.strip() or not template_id.strip():
   raise ValueError("tenant, actor, Agent and model template are required")
  if template_version < 1:
   raise ValueError("template_version must be positive")
  run_uuid=UUID(run_id)
  try:
   from psycopg.types.json import Jsonb
  except ImportError as exc:
   raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
  c=await self._connect()
  async with c:
   async with c.transaction():
    cur=await c.execute("""INSERT INTO platform_runs
     (tenant_id,run_id,project_id,task_id,agent_id,template_id,template_version,
      workflow_id,status,input,created_by,idempotency_key,request_digest)
     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s,%s)
     ON CONFLICT (tenant_id,created_by,idempotency_key)
       WHERE idempotency_key IS NOT NULL DO NOTHING
     RETURNING run_id""",
     (tenant_id,run_uuid,project_id,str(run_uuid),agent_id,template_id,
      template_version,workflow_id,Jsonb(dict(input)),actor_id,idempotency_key,request_digest))
    inserted=await cur.fetchone()
    if inserted is not None:
     return True
    if idempotency_key is None:
     raise RuntimeError("Run identifier collision")
    cur=await c.execute("""SELECT request_digest FROM platform_runs
     WHERE tenant_id=%s AND created_by=%s AND idempotency_key=%s FOR UPDATE""",
     (tenant_id,actor_id,idempotency_key))
    existing=await cur.fetchone()
    if existing is None:
     raise RuntimeError("idempotent task insertion conflict could not be resolved")
    if existing["request_digest"] != request_digest:
     raise ValueError("idempotency_key was already used with a different task request")
    return False

 async def get_task_record(self, tenant_id:str, task_id:str, *, actor_id:str|None=None)->dict[str,Any]|None:
  c=await self._connect()
  async with c:
   query="""SELECT tenant_id,run_id,task_id,project_id,agent_id,template_id,
    template_version,workflow_id,status,input,output,usage,trace_id,created_by,
    idempotency_key,request_digest,started_at,finished_at,created_at,updated_at,
    checkpoint_ref,last_error,registry_snapshot_digest
    FROM platform_runs WHERE tenant_id=%s AND task_id=%s"""
   params:list[Any]=[tenant_id,task_id]
   if actor_id is not None:
    query+=" AND created_by=%s";params.append(actor_id)
   cur=await c.execute(query,tuple(params))
   row=await cur.fetchone()
  return dict(row) if row is not None else None

 async def list_task_records(self, tenant_id:str, actor_id:str, *, limit:int=100)->list[dict[str,Any]]:
  if not 1<=limit<=200:raise ValueError("limit must be between 1 and 200")
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT tenant_id,run_id,task_id,project_id,agent_id,template_id,
    template_version,workflow_id,status,input,output,usage,trace_id,created_by,
    idempotency_key,request_digest,started_at,finished_at,created_at,updated_at,
    checkpoint_ref,last_error,registry_snapshot_digest
    FROM platform_runs WHERE tenant_id=%s AND created_by=%s
    ORDER BY created_at DESC LIMIT %s""",(tenant_id,actor_id,limit))
   rows=await cur.fetchall()
  return [dict(row) for row in rows]

 async def save_output(self, tenant_id:str, run_id:str, output:Any, *,
  usage:Mapping[str,Any]|None=None, trace_id:str|None=None)->None:
  try:
   from psycopg.types.json import Jsonb
  except ImportError as exc:
   raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs SET output=%s,usage=%s,
    trace_id=COALESCE(%s,trace_id),updated_at=now()
    WHERE tenant_id=%s AND run_id=%s AND status='succeeded' RETURNING run_id""",
    (Jsonb(output) if isinstance(output,(dict,list)) else Jsonb({"text":str(output)}),
     Jsonb(dict(usage or {})),trace_id,tenant_id,UUID(run_id)))
   if await cur.fetchone() is None:
    raise RuntimeError("cannot persist output for a Run that has not succeeded")

 async def get(self,tenant_id:str,run_id:str)->RunSnapshot|None:
  c=await self._connect()
  async with c:
   cur=await c.execute("SELECT tenant_id,run_id,status,version,checkpoint_ref,last_error,updated_at FROM platform_runs WHERE tenant_id=%s AND run_id=%s",(tenant_id,run_id))
   return self._snapshot(await cur.fetchone())
 async def transition(self,snapshot:RunSnapshot,*,expected_version:int)->RunSnapshot:
  c=await self._connect()
  async with c:
   cur=await c.execute("SELECT status,version FROM platform_runs WHERE tenant_id=%s AND run_id=%s FOR UPDATE",(snapshot.tenant_id,snapshot.run_id))
   current=await cur.fetchone()
   if current is None or int(current["version"])!=expected_version:raise RuntimeError("Run state version conflict or Run not found")
   old_status=RunStatus(current["status"])
   if snapshot.status not in _ALLOWED[old_status]:raise ValueError(f"illegal Run transition {old_status.value} -> {snapshot.status.value}")
   cur=await c.execute("""UPDATE platform_runs SET status=%s,version=version+1,checkpoint_ref=%s,last_error=%s,updated_at=now(),
    started_at=CASE WHEN %s='running' AND started_at IS NULL THEN now() ELSE started_at END,
    finished_at=CASE WHEN %s IN ('succeeded','failed','cancelled','timed_out') THEN now() ELSE finished_at END,
    worker_id=CASE WHEN %s IN ('succeeded','failed','cancelled','timed_out') THEN NULL ELSE worker_id END,
    lease_expires_at=CASE WHEN %s IN ('succeeded','failed','cancelled','timed_out') THEN NULL ELSE lease_expires_at END
    WHERE tenant_id=%s AND run_id=%s AND version=%s
    RETURNING tenant_id,run_id,status,version,checkpoint_ref,last_error,updated_at""",
    (snapshot.status.value,snapshot.checkpoint_ref,snapshot.last_error,snapshot.status.value,snapshot.status.value,
     snapshot.status.value,snapshot.status.value,snapshot.tenant_id,snapshot.run_id,expected_version))
   row=await cur.fetchone()
   if row is None:raise RuntimeError("Run state version conflict or Run not found")
   return self._snapshot(row)
 async def claim_recoverable(self,worker_id:str,*,tenant_id:str|None=None,limit:int=100,lease_seconds:int=60)->list[RunSnapshot]:
  if not worker_id.strip() or limit<1 or lease_seconds<5:raise ValueError("invalid worker lease parameters")
  c=await self._connect()
  async with c:
   cur=await c.execute("""WITH candidates AS (
    SELECT tenant_id,run_id FROM platform_runs
    WHERE ((status IN ('queued','interrupted','cancelling') AND (worker_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at<=now()))
       OR (status='running' AND (lease_expires_at IS NULL OR lease_expires_at<=now())))
      AND (%s::text IS NULL OR tenant_id=%s)
    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s
    )
    UPDATE platform_runs r SET worker_id=%s,lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
    FROM candidates c WHERE r.tenant_id=c.tenant_id AND r.run_id=c.run_id
    RETURNING r.tenant_id,r.run_id,r.status,r.version,r.checkpoint_ref,r.last_error,r.updated_at""",
    (tenant_id,tenant_id,limit,worker_id,lease_seconds))
   return [self._snapshot(row) for row in await cur.fetchall()]
 async def acquire_lease(self,tenant_id:str,run_id:str,worker_id:str,*,lease_seconds:int=60)->bool:
  if not worker_id.strip() or lease_seconds<5:raise ValueError("invalid worker lease parameters")
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs SET worker_id=%s,lease_expires_at=now()+(%s * interval '1 second')
    WHERE tenant_id=%s AND run_id=%s AND status IN ('queued','running')
      AND (worker_id IS NULL OR worker_id=%s OR lease_expires_at<=now()) RETURNING run_id""",
    (worker_id,lease_seconds,tenant_id,run_id,worker_id))
   return await cur.fetchone() is not None
 async def heartbeat(self,tenant_id:str,run_id:str,worker_id:str,*,lease_seconds:int=60)->bool:
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs SET lease_expires_at=now()+(%s * interval '1 second')
    WHERE tenant_id=%s AND run_id=%s AND worker_id=%s AND lease_expires_at>now() AND status='running'
    RETURNING run_id""",(lease_seconds,tenant_id,run_id,worker_id))
   return await cur.fetchone() is not None
 async def release_lease(self,tenant_id:str,run_id:str,worker_id:str)->bool:
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs SET worker_id=NULL,lease_expires_at=NULL
    WHERE tenant_id=%s AND run_id=%s AND worker_id=%s RETURNING run_id""",(tenant_id,run_id,worker_id))
   return await cur.fetchone() is not None
 async def recoverable(self,limit:int=100)->list[RunSnapshot]:
  if limit<1:raise ValueError("limit must be positive")
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT tenant_id,run_id,status,version,checkpoint_ref,last_error,updated_at FROM platform_runs
    WHERE status='paused'
       OR (status IN ('queued','interrupted','cancelling') AND (worker_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at<=now()))
       OR (status='running' AND (lease_expires_at IS NULL OR lease_expires_at<=now()))
    ORDER BY created_at LIMIT %s""",(limit,))
   return [self._snapshot(row) for row in await cur.fetchall()]
