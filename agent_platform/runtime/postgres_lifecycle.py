"""PostgreSQL Run state store with optimistic transitions and worker leases."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

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
  return RunSnapshot(
    row["tenant_id"],str(row["run_id"]),RunStatus(row["status"]),int(row["version"]),
    row.get("checkpoint_ref"),row.get("last_error"),row.get("updated_at"),
    row.get("output"),row.get("input"),row.get("usage"),int(row.get("lease_token") or 0),
  )
 async def create_task_run(self, *, tenant_id:str, run_id:str, actor_id:str,
  agent_id:str, agent_version:int, template_id:str, template_version:int, input:Mapping[str,Any],
  preparation_owner:str, registry_snapshot:Sequence[Mapping[str,Any]], priority:int=100,
  project_id:str|None=None, session_id:str|None=None, workflow_id:str|None=None,
  idempotency_key:str|None=None, request_digest:str|None=None,
  parent_run_id:str|None=None, budget_lineage_key:str|None=None)->bool:
  if not tenant_id.strip() or not actor_id.strip() or not agent_id.strip() or not template_id.strip():
   raise ValueError("tenant, actor, Agent and model template are required")
  if not preparation_owner.strip():
   raise ValueError("preparation_owner is required")
  if type(priority) is not int or not 0 <= priority <= 1000:
   raise ValueError("priority must be an integer between 0 and 1000")
  if agent_version < 1 or template_version < 1:
   raise ValueError("Agent and model template versions must be positive")
  run_uuid=UUID(run_id)
  if (parent_run_id is None) != (budget_lineage_key is None):
   raise ValueError("parent_run_id and budget_lineage_key must be supplied together")
  UUID(parent_run_id) if parent_run_id is not None else None
  if budget_lineage_key is not None and not budget_lineage_key.strip():
   raise ValueError("budget_lineage_key must be non-empty")
  try:
   from psycopg.types.json import Jsonb
  except ImportError as exc:
   raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
  c=await self._connect()
  async with c:
   async with c.transaction():
    cur=await c.execute("""INSERT INTO platform_runs
     (tenant_id,run_id,project_id,session_id,task_id,agent_id,agent_version,template_id,template_version,
      workflow_id,status,priority,input,created_by,idempotency_key,request_digest,registry_snapshot,dispatch_ready,
      preparation_owner,preparation_lease_expires_at)
     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s,%s,%s,%s,false,%s,now()+(60 * interval '1 second'))
     ON CONFLICT (tenant_id,created_by,idempotency_key)
       WHERE idempotency_key IS NOT NULL DO NOTHING
     RETURNING run_id""",
     (tenant_id,run_uuid,project_id,session_id,str(run_uuid),agent_id,agent_version,template_id,
      template_version,workflow_id,priority,Jsonb(dict(input)),actor_id,idempotency_key,request_digest,
      Jsonb([dict(item) for item in registry_snapshot]),preparation_owner))
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

 async def create_retry_task_run(
  self, *, tenant_id:str, task_id:str, actor_id:str, run_id:str,
  idempotency_key:str, request_digest:str,
 )->str:
  """Create a new Run from a failed task while preserving its pinned registry snapshot."""
  run_uuid=UUID(run_id)
  c=await self._connect()
  async with c:
   async with c.transaction():
    cur=await c.execute(
     """SELECT run_id,status,registry_snapshot,registry_snapshot_digest,input
        FROM platform_runs
        WHERE tenant_id=%s AND task_id=%s
        FOR UPDATE""",
     (tenant_id,task_id),
    )
    source=await cur.fetchone()
    if source is None:
     raise KeyError("task not found")
    if source["status"] not in ("failed","timed_out"):
     raise ValueError("only failed or timed_out tasks can be retried")
    if source["registry_snapshot"] is None or not source["registry_snapshot_digest"]:
     raise ValueError("task has no immutable registry snapshot and cannot be retried")
    # 防重复：同一 task 已有 queued/running 的 retry run → 直接返回
    cur=await c.execute(
     """SELECT run_id FROM platform_runs
        WHERE tenant_id=%s AND task_id=%s AND retry_of_run_id IS NOT NULL
          AND status IN ('queued','running')
        LIMIT 1""",
     (tenant_id,task_id),
    )
    existing=await cur.fetchone()
    if existing is not None:
     return str(existing["run_id"])
    # 快照摘要的规范化内容包含 run_id，重试必须按新运行重算，否则校验必然失败。
    entries=source["registry_snapshot"]
    digest=source["registry_snapshot_digest"]
    if isinstance(entries,list) and entries and all(
      isinstance(entry,dict) and "definition" in entry for entry in entries
    ):
     canonical=json.dumps(
      {"tenant_id":tenant_id,"run_id":str(run_uuid),
       "entries":[dict(entry) for entry in entries]},
      ensure_ascii=False,sort_keys=True,separators=(",",":"),
     )
     digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    cur=await c.execute(
     """INSERT INTO platform_runs
        (tenant_id,run_id,project_id,session_id,task_id,agent_id,agent_version,
         template_id,template_version,workflow_id,status,priority,input,created_by,
         idempotency_key,request_digest,registry_snapshot,registry_snapshot_digest,
         dispatch_ready,retry_of_run_id)
        SELECT tenant_id,%s,project_id,session_id,%s::uuid,agent_id,agent_version,
         template_id,template_version,workflow_id,'queued',priority,input,created_by,
         %s,%s,registry_snapshot,%s,true,%s
        FROM platform_runs
        WHERE tenant_id=%s AND run_id=%s
        ON CONFLICT (tenant_id,created_by,idempotency_key)
          WHERE idempotency_key IS NOT NULL DO NOTHING
        RETURNING run_id""",
     (run_uuid,task_id,idempotency_key,request_digest,digest,source["run_id"],
      tenant_id,source["run_id"]),
    )
    inserted=await cur.fetchone()
    if inserted is not None:
     return str(inserted["run_id"])
    cur=await c.execute(
     """SELECT run_id,request_digest FROM platform_runs
        WHERE tenant_id=%s AND created_by=%s AND idempotency_key=%s
        FOR UPDATE""",
     (tenant_id,actor_id,idempotency_key),
    )
    existing=await cur.fetchone()
    if existing is None:
     raise RuntimeError("idempotent retry insertion conflict could not be resolved")
    if existing["request_digest"] != request_digest:
     raise ValueError("idempotency_key was already used with a different retry request")
    return str(existing["run_id"])

 async def get_task_record(self, tenant_id:str, task_id:str, *, actor_id:str|None=None)->dict[str,Any]|None:
  c=await self._connect()
  async with c:
   query="""SELECT tenant_id,run_id,task_id,project_id,session_id,agent_id,agent_version,template_id,
    template_version,workflow_id,status,priority,input,output,usage,trace_id,created_by,retry_of_run_id,
    idempotency_key,request_digest,registry_snapshot,started_at,finished_at,created_at,updated_at,
    checkpoint_ref,last_error,registry_snapshot_digest,dispatch_ready
    FROM platform_runs WHERE tenant_id=%s AND task_id=%s"""
   params:list[Any]=[tenant_id,task_id]
   if actor_id is not None:
    query+=" AND created_by=%s";params.append(actor_id)
   cur=await c.execute(query,tuple(params))
   row=await cur.fetchone()
  return dict(row) if row is not None else None

 async def get_run_record(self,tenant_id:str,run_id:str)->dict[str,Any]|None:
  """Load the tenant-scoped source record for event consumers."""
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT tenant_id,run_id,task_id,session_id,project_id,agent_id,
    status,input,output,created_by FROM platform_runs
    WHERE tenant_id=%s AND run_id=%s""",(tenant_id,UUID(run_id)))
   row=await cur.fetchone()
  return dict(row) if row is not None else None

 async def get_task_by_idempotency(self,tenant_id:str,actor_id:str,idempotency_key:str)->dict[str,Any]|None:
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT tenant_id,run_id,task_id,project_id,session_id,agent_id,agent_version,template_id,
    template_version,workflow_id,status,priority,input,output,usage,trace_id,created_by,retry_of_run_id,
    idempotency_key,request_digest,started_at,finished_at,created_at,updated_at,
    checkpoint_ref,last_error,registry_snapshot_digest,dispatch_ready
    FROM platform_runs WHERE tenant_id=%s AND created_by=%s AND idempotency_key=%s""",
    (tenant_id,actor_id,idempotency_key))
   row=await cur.fetchone()
  return dict(row) if row is not None else None

 async def list_task_records(self, tenant_id:str, actor_id:str, *, limit:int=100)->list[dict[str,Any]]:
  if not 1<=limit<=200:raise ValueError("limit must be between 1 and 200")
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT tenant_id,run_id,task_id,project_id,session_id,agent_id,agent_version,template_id,
    template_version,workflow_id,status,priority,input,output,usage,trace_id,created_by,retry_of_run_id,
    idempotency_key,request_digest,started_at,finished_at,created_at,updated_at,
    checkpoint_ref,last_error,registry_snapshot_digest,dispatch_ready
    FROM platform_runs WHERE tenant_id=%s AND created_by=%s
    ORDER BY created_at DESC LIMIT %s""",(tenant_id,actor_id,limit))
   rows=await cur.fetchall()
  return [dict(row) for row in rows]

 async def list_runs_by_task(self, tenant_id:str, task_id:str)->list[dict[str,Any]]:
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT run_id,task_id,status,agent_id,started_at,finished_at,
    last_error,updated_at,usage,registry_snapshot_digest,trace_id,created_by
    FROM platform_runs WHERE tenant_id=%s AND task_id=%s
    ORDER BY created_at ASC""",(tenant_id,task_id))
   rows=await cur.fetchall()
  result:list[dict[str,Any]]=[]
  for r in rows:
    d=dict(r)
    s=d.get("started_at")
    f=d.get("finished_at")
    if s and f:
      try:d["duration_ms"]=int((f-s).total_seconds()*1000)
      except Exception:d["duration_ms"]=None
    else:d["duration_ms"]=None
    result.append(d)
  return result

 async def claim_task_preparation(self,tenant_id:str,run_id:str,owner:str,*,lease_seconds:int=60)->bool:
  if not owner.strip() or lease_seconds<5:
   raise ValueError("invalid task preparation lease")
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs
    SET preparation_owner=%s,
        preparation_lease_expires_at=now()+(%s * interval '1 second')
    WHERE tenant_id=%s AND run_id=%s AND status='queued'
      AND dispatch_ready=false
      AND (preparation_owner IS NULL OR preparation_lease_expires_at IS NULL
           OR preparation_lease_expires_at<=now())
    RETURNING run_id""",(owner,lease_seconds,tenant_id,UUID(run_id)))
   return await cur.fetchone() is not None

 async def owns_task_preparation(self,tenant_id:str,run_id:str,owner:str)->bool:
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT 1 FROM platform_runs
    WHERE tenant_id=%s AND run_id=%s AND status='queued'
      AND dispatch_ready=false AND preparation_owner=%s
      AND preparation_lease_expires_at>now()""",
    (tenant_id,UUID(run_id),owner))
   return await cur.fetchone() is not None

 async def mark_task_ready(self,tenant_id:str,run_id:str,digest:str,owner:str)->bool:
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs SET dispatch_ready=true,
      preparation_owner=NULL,preparation_lease_expires_at=NULL,updated_at=now()
    WHERE tenant_id=%s AND run_id=%s AND status='queued'
      AND preparation_owner=%s
      AND registry_snapshot IS NOT NULL AND registry_snapshot_digest=%s
    RETURNING run_id""",(tenant_id,UUID(run_id),owner,digest))
   return await cur.fetchone() is not None

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
   cur=await c.execute(
     "SELECT tenant_id,run_id,status,version,checkpoint_ref,last_error,updated_at,output,input,usage,lease_token "
     "FROM platform_runs WHERE tenant_id=%s AND run_id=%s",
     (tenant_id,run_id),
   )
   return self._snapshot(await cur.fetchone())
 async def transition(self,snapshot:RunSnapshot,*,expected_version:int)->RunSnapshot:
  c=await self._connect()
  async with c:
   cur=await c.execute("SELECT status,version,session_id,created_by,project_id,agent_id,lease_token FROM platform_runs WHERE tenant_id=%s AND run_id=%s FOR UPDATE",(snapshot.tenant_id,snapshot.run_id))
   current=await cur.fetchone()
   if current is None or int(current["version"])!=expected_version:raise RuntimeError("Run state version conflict or Run not found")
   if snapshot.lease_token > 0 and int(current.get("lease_token") or 0) != snapshot.lease_token:
    raise RuntimeError("Run lease fencing token mismatch; stale worker write rejected")
   old_status=RunStatus(current["status"])
   if snapshot.status not in _ALLOWED[old_status]:raise ValueError(f"illegal Run transition {old_status.value} -> {snapshot.status.value}")
   cur=await c.execute("""UPDATE platform_runs SET status=%s,version=version+1,checkpoint_ref=%s,last_error=%s,updated_at=now(),
    started_at=CASE WHEN %s='running' AND started_at IS NULL THEN now() ELSE started_at END,
    finished_at=CASE WHEN %s IN ('succeeded','failed','cancelled','timed_out') THEN now() ELSE finished_at END,
    worker_id=CASE WHEN %s IN ('succeeded','failed','cancelled','timed_out','interrupted','paused') THEN NULL ELSE worker_id END,
    lease_expires_at=CASE WHEN %s IN ('succeeded','failed','cancelled','timed_out','interrupted','paused') THEN NULL ELSE lease_expires_at END,
    preparation_owner=CASE WHEN %s IN ('succeeded','failed','cancelled','timed_out','interrupted','paused') THEN NULL ELSE preparation_owner END,
    preparation_lease_expires_at=CASE WHEN %s IN ('succeeded','failed','cancelled','timed_out','interrupted','paused') THEN NULL ELSE preparation_lease_expires_at END
    WHERE tenant_id=%s AND run_id=%s AND version=%s
      AND (%s::bigint IS NULL OR lease_token=%s)
    RETURNING tenant_id,run_id,status,version,checkpoint_ref,last_error,updated_at,lease_token""",
    (snapshot.status.value,snapshot.checkpoint_ref,snapshot.last_error,snapshot.status.value,snapshot.status.value,
     snapshot.status.value,snapshot.status.value,snapshot.status.value,snapshot.status.value,
     snapshot.tenant_id,snapshot.run_id,expected_version,
     snapshot.lease_token if snapshot.lease_token > 0 else None,
     snapshot.lease_token if snapshot.lease_token > 0 else None))
   row=await cur.fetchone()
   if row is None:raise RuntimeError("Run state version conflict or Run not found")
   try:
    from psycopg.types.json import Jsonb
   except ImportError as exc:
    raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
   await c.execute(
    """INSERT INTO platform_run_events
       (tenant_id,run_id,event_type,payload)
       VALUES (%s,%s,'run.status_changed',%s)""",
    (snapshot.tenant_id,snapshot.run_id,Jsonb({
     "from_status":old_status.value,"to_status":snapshot.status.value,
     "version":int(row["version"]),
    })),
   )
   # Queue memory extraction in the same transaction as the terminal state
   # transition. The event contains identifiers only; the consumer must reload
   # the owned session turn and enforce the memory feature/policy before use.
   if snapshot.status == RunStatus.SUCCEEDED and current.get("session_id"):
    event_id = uuid4()
    payload = {
     "run_id": snapshot.run_id,
     "session_id": current["session_id"],
     "actor_id": current["created_by"],
     "project_id": current.get("project_id"),
     "agent_id": current.get("agent_id"),
    }
    await c.execute(
     """INSERT INTO platform_outbox
        (tenant_id,event_id,topic,payload)
        VALUES (%s,%s,'memory.extraction.requested',%s)
        ON CONFLICT (tenant_id,event_id) DO NOTHING""",
     (snapshot.tenant_id,event_id,Jsonb(payload)),
    )
   return self._snapshot(row)
 async def claim_recoverable(self,worker_id:str,*,tenant_id:str|None=None,limit:int=100,lease_seconds:int=60)->list[RunSnapshot]:
  if not worker_id.strip() or limit<1 or lease_seconds<5:raise ValueError("invalid worker lease parameters")
  c=await self._connect()
  async with c:
   cur=await c.execute("""WITH candidates AS (
    SELECT tenant_id,run_id FROM platform_runs
    WHERE task_id IS NULL AND ((status IN ('queued','interrupted') AND (worker_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at<=now()))
       OR (status='running' AND (lease_expires_at IS NULL OR lease_expires_at<=now())))
      AND (%s::text IS NULL OR tenant_id=%s)
    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s
    )
    UPDATE platform_runs r SET worker_id=%s,lease_token=r.lease_token+1,lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
    FROM candidates c WHERE r.tenant_id=c.tenant_id AND r.run_id=c.run_id
    RETURNING r.tenant_id,r.run_id,r.status,r.version,r.checkpoint_ref,r.last_error,r.updated_at,r.lease_token""",
    (tenant_id,tenant_id,limit,worker_id,lease_seconds))
   return [self._snapshot(row) for row in await cur.fetchall()]
 async def claim_workbench_tasks(self,worker_id:str,*,tenant_id:str|None=None,limit:int=20,lease_seconds:int=60)->list[RunSnapshot]:
  if not worker_id.strip() or not 1<=limit<=200 or lease_seconds<5:
   raise ValueError("invalid workbench worker lease parameters")
  c=await self._connect()
  async with c:
   cur=await c.execute("""
    WITH
    -- 清僵尸：attempts >= max 且没有有效租约的 run 永远不会再被 claim，
    -- 直接晋级 failed 防止堆积。包含 status='running' 的失联 run，
    -- 以及 task_id IS NULL 的孤儿 run。
    _zombie AS (
      UPDATE platform_runs SET
        status='failed',
        last_error='运行已终止：重试次数已用尽，未能正常收尾',
        dead_lettered_at=now(),
        dead_letter_reason='exhausted_attempts_never_finalized',
        finished_at=now(),
        worker_id=NULL, lease_expires_at=NULL, updated_at=now()
      WHERE (%s::text IS NULL OR tenant_id=%s)
        AND COALESCE(worker_attempts,0) >= COALESCE(max_worker_attempts,5)
        AND status IN ('queued','interrupted','running','cancelling')
        AND (lease_expires_at IS NULL OR lease_expires_at<=now())
    ),
    candidates AS (
    SELECT tenant_id,run_id FROM platform_runs
    WHERE task_id IS NOT NULL AND dispatch_ready=true
      AND (%s::text IS NULL OR tenant_id=%s)
      AND (status='cancelling' OR COALESCE(worker_attempts,0) < COALESCE(max_worker_attempts,5))
      AND ((status IN ('queued','interrupted','cancelling')
            AND (worker_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at<=now()))
        OR (status='running' AND (lease_expires_at IS NULL OR lease_expires_at<=now())))
    ORDER BY priority DESC,created_at ASC FOR UPDATE SKIP LOCKED LIMIT %s
    ),
    claimed AS (
    UPDATE platform_runs r SET worker_id=%s,
      worker_attempts=COALESCE(r.worker_attempts,0)+1,
      lease_token=r.lease_token+1,
      lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
    FROM candidates c WHERE r.tenant_id=c.tenant_id AND r.run_id=c.run_id
    RETURNING r.tenant_id,r.run_id,r.status,r.version,r.checkpoint_ref,r.last_error,r.updated_at,r.priority,r.lease_token
    )
    SELECT tenant_id,run_id,status,version,checkpoint_ref,last_error,updated_at,lease_token
    FROM claimed ORDER BY priority DESC,run_id ASC""",
    (tenant_id,tenant_id,tenant_id,tenant_id,limit,worker_id,lease_seconds))
   return [self._snapshot(row) for row in await cur.fetchall()]

 async def acquire_lease(self,tenant_id:str,run_id:str,worker_id:str,*,lease_seconds:int=60)->bool:
  if not worker_id.strip() or lease_seconds<5:raise ValueError("invalid worker lease parameters")
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs SET worker_id=%s,lease_token=lease_token+1,lease_expires_at=now()+(%s * interval '1 second')
    WHERE tenant_id=%s AND run_id=%s AND status IN ('queued','running')
      AND (worker_id IS NULL OR worker_id=%s OR lease_expires_at<=now()) RETURNING run_id""",
    (worker_id,lease_seconds,tenant_id,run_id,worker_id))
   return await cur.fetchone() is not None
 async def owns_lease(self,tenant_id:str,run_id:str,worker_id:str,*,lease_token:int)->bool:
  if type(lease_token) is not int or lease_token<1:
   raise ValueError("lease_token must be a positive integer")
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT 1 FROM platform_runs
    WHERE tenant_id=%s AND run_id=%s AND worker_id=%s
      AND lease_expires_at>now() AND lease_token=%s""",(tenant_id,run_id,worker_id,lease_token))
   return await cur.fetchone() is not None

 async def heartbeat(self,tenant_id:str,run_id:str,worker_id:str,*,lease_token:int,lease_seconds:int=60)->bool:
  if type(lease_token) is not int or lease_token<1:
   raise ValueError("lease_token must be a positive integer")
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs SET lease_expires_at=now()+(%s * interval '1 second')
    WHERE tenant_id=%s AND run_id=%s AND worker_id=%s AND lease_expires_at>now() AND status='running'
      AND lease_token=%s
    RETURNING run_id""",(lease_seconds,tenant_id,run_id,worker_id,lease_token))
   return await cur.fetchone() is not None
 async def record_workbench_failure(self,tenant_id:str,run_id:str,worker_id:str,*,error:str,lease_token:int)->str|None:
  """Retry an owned task after failure, or atomically move exhausted work to the DLQ."""
  if type(lease_token) is not int or lease_token<1:
   raise ValueError("lease_token must be a positive integer")
  c=await self._connect()
  async with c:
   cur=await c.execute("""WITH updated AS (
    UPDATE platform_runs SET
      status=CASE WHEN worker_attempts>=max_worker_attempts THEN 'failed' ELSE 'interrupted' END,
      last_error=%s,
      dead_lettered_at=CASE WHEN worker_attempts>=max_worker_attempts THEN now() ELSE NULL END,
      dead_letter_reason=CASE WHEN worker_attempts>=max_worker_attempts THEN %s ELSE NULL END,
      finished_at=CASE WHEN worker_attempts>=max_worker_attempts THEN now() ELSE NULL END,
      worker_id=NULL,lease_expires_at=NULL,version=version+1,updated_at=now()
    WHERE tenant_id=%s AND run_id=%s AND worker_id=%s
      AND lease_token=%s
      AND status NOT IN ('succeeded','failed','cancelled','timed_out')
    RETURNING tenant_id,run_id,status,worker_attempts,max_worker_attempts,last_error
   ), dead_letter AS (
    INSERT INTO platform_run_dead_letters
      (tenant_id,run_id,attempt_count,max_attempts,reason,payload)
    SELECT tenant_id,run_id,worker_attempts,max_worker_attempts,last_error,
      jsonb_build_object('source','workbench_worker','run_id',run_id)
    FROM updated WHERE status='failed'
    ON CONFLICT (tenant_id,run_id) DO UPDATE SET
      attempt_count=EXCLUDED.attempt_count,max_attempts=EXCLUDED.max_attempts,
      reason=EXCLUDED.reason,payload=EXCLUDED.payload,updated_at=now()
    RETURNING run_id
   )
   SELECT status FROM updated""",(error[:500],error[:500],tenant_id,UUID(run_id),worker_id,lease_token))
   row=await cur.fetchone()
   return row["status"] if row is not None else None

 async def ensure_dead_letter(self,tenant_id:str,run_id:str,*,reason:str|None=None)->bool:
  """把终态失败的运行补登记到死信队列（幂等）。

  执行器直接判定失败时不会经过 worker 的重试计数分支，因此需要单独补登记，
  否则死信队列永远为空、人工重放无从触发。
  """
  c=await self._connect()
  async with c:
   cur=await c.execute("""INSERT INTO platform_run_dead_letters
     (tenant_id,run_id,attempt_count,max_attempts,reason,payload)
    SELECT tenant_id,run_id,COALESCE(worker_attempts,1),COALESCE(max_worker_attempts,1),
      COALESCE(%s,last_error,'未记录失败原因'),
      jsonb_build_object('source','workbench_executor','run_id',run_id)
    FROM platform_runs WHERE tenant_id=%s AND run_id=%s AND status IN ('failed','timed_out')
    ON CONFLICT (tenant_id,run_id) DO UPDATE SET
      attempt_count=EXCLUDED.attempt_count,max_attempts=EXCLUDED.max_attempts,
      reason=EXCLUDED.reason,payload=EXCLUDED.payload,resolved_at=NULL,resolved_by=NULL,
      updated_at=now()
    RETURNING run_id""",(reason,tenant_id,UUID(run_id)))
   return await cur.fetchone() is not None

 async def release_lease(self,tenant_id:str,run_id:str,worker_id:str,*,lease_token:int)->bool:
  if type(lease_token) is not int or lease_token<1:
   raise ValueError("lease_token must be a positive integer")
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs SET worker_id=NULL,lease_expires_at=NULL
    WHERE tenant_id=%s AND run_id=%s AND worker_id=%s
      AND lease_token=%s RETURNING run_id""",(tenant_id,run_id,worker_id,lease_token))
   return await cur.fetchone() is not None
 async def tick_worker_heartbeat(self,worker_id:str)->None:
  """Ensure this worker is visible to ops monitoring even when idle.

  If we already have a row, bump its updated_at. Otherwise INSERT a tiny
  "heartbeat marker" row so ops panels see we're alive (status='succeeded'
  keeps it out of active processing queries).
  """
  if not worker_id.strip():return
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_runs SET updated_at=now()
    WHERE run_id=(SELECT run_id FROM platform_runs
                  WHERE worker_id=%s ORDER BY updated_at DESC LIMIT 1)""",(worker_id,))
   if cur.rowcount==0:
    # First run for this worker: insert a marker row.
    from uuid import uuid4
    cur2=await c.execute("""INSERT INTO platform_runs
      (tenant_id,run_id,status,worker_id,created_at,updated_at,lease_expires_at)
      VALUES ('fde',%s,'succeeded',%s,now(),now(),NULL)
      ON CONFLICT DO NOTHING""",(uuid4(),worker_id))
    await cur2.fetchall()

 async def recoverable(self,limit:int=100)->list[RunSnapshot]:
  if limit<1:raise ValueError("limit must be positive")
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT tenant_id,run_id,status,version,checkpoint_ref,last_error,updated_at FROM platform_runs
    WHERE task_id IS NULL AND (status='paused'
       OR (status IN ('queued','interrupted') AND (worker_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at<=now()))
       OR (status='running' AND (lease_expires_at IS NULL OR lease_expires_at<=now())))
    ORDER BY created_at LIMIT %s""",(limit,))
   return [self._snapshot(row) for row in await cur.fetchall()]

 async def list_recent(self,tenant_id:str,limit:int=20)->list[RunSnapshot]:
  limit=max(1,min(int(limit),200))
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT tenant_id,run_id,status,version,checkpoint_ref,last_error,updated_at,output,input,usage
    FROM platform_runs WHERE tenant_id=%s ORDER BY COALESCE(updated_at,created_at) DESC LIMIT %s""",(tenant_id,limit))
   return [self._snapshot(row) for row in await cur.fetchall()]

 async def list_dead_letters(self,tenant_id:str,*,limit:int=50,include_resolved:bool=False)->list[dict[str,Any]]:
  limit=max(1,min(int(limit),200))
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT d.run_id,d.attempt_count,d.max_attempts,d.reason,d.payload,
     d.created_at,d.resolved_at,d.resolved_by,r.task_id,r.agent_id,r.created_by
    FROM platform_run_dead_letters d
    LEFT JOIN platform_runs r ON r.tenant_id=d.tenant_id AND r.run_id=d.run_id
    WHERE d.tenant_id=%s AND (%s OR d.resolved_at IS NULL)
    ORDER BY d.created_at DESC LIMIT %s""",(tenant_id,include_resolved,limit))
   return [dict(row) for row in await cur.fetchall()]

 async def dead_letter(self,tenant_id:str,run_id:str)->dict[str,Any]|None:
  c=await self._connect()
  async with c:
   cur=await c.execute("""SELECT d.run_id,d.attempt_count,d.max_attempts,d.reason,d.payload,
     d.created_at,d.resolved_at,d.resolved_by,r.task_id,r.agent_id,r.created_by
    FROM platform_run_dead_letters d
    LEFT JOIN platform_runs r ON r.tenant_id=d.tenant_id AND r.run_id=d.run_id
    WHERE d.tenant_id=%s AND d.run_id=%s""",(tenant_id,UUID(run_id)))
   row=await cur.fetchone()
   return dict(row) if row is not None else None

 async def resolve_dead_letter(self,tenant_id:str,run_id:str,resolved_by:str)->dict[str,Any]|None:
  c=await self._connect()
  async with c:
   cur=await c.execute("""UPDATE platform_run_dead_letters
    SET resolved_at=COALESCE(resolved_at,now()),resolved_by=COALESCE(resolved_by,%s),updated_at=now()
    WHERE tenant_id=%s AND run_id=%s
    RETURNING run_id,resolved_at,resolved_by""",(resolved_by,tenant_id,UUID(run_id)))
   row=await cur.fetchone()
   return dict(row) if row is not None else None
