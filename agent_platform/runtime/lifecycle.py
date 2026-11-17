"""Run state transitions and cancellation propagation around Harness/LangGraph."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


def _now() -> datetime: return datetime.now(timezone.utc)
class WorkerLeaseLost(RuntimeError):
 """Raised when a worker no longer owns the Run execution lease."""
class RunStatus(str,Enum):
    QUEUED="queued"; RUNNING="running"; PAUSED="paused"; CANCELLING="cancelling"
    SUCCEEDED="succeeded"; FAILED="failed"; CANCELLED="cancelled"; TIMED_OUT="timed_out"; INTERRUPTED="interrupted"
TERMINAL={RunStatus.SUCCEEDED,RunStatus.FAILED,RunStatus.CANCELLED,RunStatus.TIMED_OUT}
_ALLOWED={
 RunStatus.QUEUED:{RunStatus.RUNNING,RunStatus.CANCELLED,RunStatus.FAILED},
 RunStatus.RUNNING:{RunStatus.PAUSED,RunStatus.CANCELLING,RunStatus.SUCCEEDED,RunStatus.FAILED,RunStatus.TIMED_OUT,RunStatus.INTERRUPTED},
 RunStatus.PAUSED:{RunStatus.RUNNING,RunStatus.CANCELLED,RunStatus.INTERRUPTED},
 RunStatus.CANCELLING:{RunStatus.CANCELLED,RunStatus.FAILED,RunStatus.TIMED_OUT},
 RunStatus.INTERRUPTED:{RunStatus.RUNNING,RunStatus.CANCELLED,RunStatus.FAILED},
 RunStatus.SUCCEEDED:set(),RunStatus.FAILED:set(),RunStatus.CANCELLED:set(),RunStatus.TIMED_OUT:set()}
@dataclass(frozen=True)
class RunSnapshot:
 tenant_id:str; run_id:str; status:RunStatus; version:int
 checkpoint_ref:str|None=None; last_error:str|None=None; updated_at:datetime|None=None
class RunStateStore(Protocol):
 async def get(self,tenant_id:str,run_id:str)->RunSnapshot|None: ...
 async def transition(self,snapshot:RunSnapshot,*,expected_version:int)->RunSnapshot: ...
 async def recoverable(self,limit:int=100)->list[RunSnapshot]: ...
class InMemoryRunStateStore:
 def __init__(self)->None: self._rows={}; self._lock=asyncio.Lock()
 async def create(self,tenant_id:str,run_id:str)->RunSnapshot:
  async with self._lock:
   key=(tenant_id,run_id)
   if key in self._rows: raise ValueError("Run already exists")
   row=RunSnapshot(tenant_id,run_id,RunStatus.QUEUED,1,updated_at=_now()); self._rows[key]=row; return row
 async def get(self,tenant_id:str,run_id:str)->RunSnapshot|None:
  async with self._lock: return self._rows.get((tenant_id,run_id))
 async def transition(self,snapshot:RunSnapshot,*,expected_version:int)->RunSnapshot:
  async with self._lock:
   key=(snapshot.tenant_id,snapshot.run_id); old=self._rows.get(key)
   if old is None or old.version!=expected_version: raise RuntimeError("Run state version conflict")
   if snapshot.status not in _ALLOWED[old.status]: raise ValueError(f"illegal Run transition {old.status.value} -> {snapshot.status.value}")
   row=RunSnapshot(snapshot.tenant_id,snapshot.run_id,snapshot.status,old.version+1,snapshot.checkpoint_ref,snapshot.last_error,_now())
   self._rows[key]=row; return row
 async def heartbeat(self,tenant_id:str,run_id:str,worker_id:str,*,lease_seconds:int=60)->bool:
  """In-memory adapter heartbeat; durable ownership is enforced by PostgresRunStateStore."""
  if lease_seconds<5 or not worker_id.strip():
   raise ValueError("invalid worker lease parameters")
  async with self._lock:
   row=self._rows.get((tenant_id,run_id))
   return row is not None and row.status==RunStatus.RUNNING
 async def recoverable(self,limit:int=100)->list[RunSnapshot]:
  if limit<1: raise ValueError("limit must be positive")
  async with self._lock: return [r for r in self._rows.values() if r.status in {RunStatus.QUEUED,RunStatus.INTERRUPTED,RunStatus.PAUSED}][:limit]
class ToolCallRegistry:
 """Tracks in-flight calls by tenant and Run; cancellation cannot cross tenant boundaries."""
 def __init__(self)->None: self._calls={}; self._lock=asyncio.Lock()
 async def run(self,run_id:str,call_id:str,awaitable:Any,*,tenant_id:str="",timeout:float|None=None)->Any:
  key=(tenant_id,run_id,call_id)
  async with self._lock:
   if key in self._calls: raise ValueError("duplicate active tool call id")
   task=asyncio.create_task(awaitable); self._calls[key]=task
  try: return await asyncio.wait_for(task,timeout) if timeout is not None else await task
  finally:
   async with self._lock: self._calls.pop(key,None)
 async def cancel_run(self,run_id:str,*,tenant_id:str|None=None)->int:
  async with self._lock:
   tasks=[t for (tenant,rid,_),t in self._calls.items()
    if rid==run_id and (tenant_id is None or tenant==tenant_id)]
   for task in tasks: task.cancel()
  if tasks: await asyncio.gather(*tasks,return_exceptions=True)
  return len(tasks)
class RunLifecycleManager:
 def __init__(self,store:RunStateStore,*,tools:ToolCallRegistry|None=None,token_service:Any|None=None)->None:
  self.store=store; self.tools=tools or ToolCallRegistry(); self.token_service=token_service; self._tasks={}; self._lock=asyncio.Lock()
 async def finish(self,tenant_id:str,run_id:str,status:RunStatus,*,checkpoint_ref:str|None=None,error:str|None=None)->RunSnapshot:
  if status not in TERMINAL: raise ValueError("finish requires a terminal Run status")
  old=await self.store.get(tenant_id,run_id)
  if old is None: raise KeyError("Run not found")
  if old.status in TERMINAL:
   if self.token_service is not None: await self.token_service.revoke_run(tenant_id=tenant_id,run_id=run_id)
   return old
  updated=await self.store.transition(RunSnapshot(tenant_id,run_id,status,old.version,
   checkpoint_ref=checkpoint_ref or old.checkpoint_ref,last_error=error),expected_version=old.version)
  if self.token_service is not None:
   await self.token_service.revoke_run(tenant_id=tenant_id,run_id=run_id)
  return updated
 async def start(self,tenant_id:str,run_id:str)->RunSnapshot:
  old=await self.store.get(tenant_id,run_id)
  if old is None: raise KeyError("Run not found")
  return await self.store.transition(RunSnapshot(tenant_id,run_id,RunStatus.RUNNING,old.version),expected_version=old.version)
 async def cancel(self,tenant_id:str,run_id:str)->RunSnapshot:
  old=await self.store.get(tenant_id,run_id)
  if old is None: raise KeyError("Run not found")
  if old.status in TERMINAL:
   if self.token_service is not None: await self.token_service.revoke_run(tenant_id=tenant_id,run_id=run_id)
   return old
  if old.status==RunStatus.CANCELLING:
   updated=old
  else:
   target=RunStatus.CANCELLED if old.status in {RunStatus.QUEUED,RunStatus.PAUSED,RunStatus.INTERRUPTED} else RunStatus.CANCELLING
   updated=await self.finish(tenant_id,run_id,target) if target==RunStatus.CANCELLED else await self.store.transition(RunSnapshot(tenant_id,run_id,target,old.version),expected_version=old.version)
  await self.tools.cancel_run(run_id,tenant_id=tenant_id)
  async with self._lock: task=self._tasks.get((tenant_id,run_id))
  if task is not None:
   task.cancel()
  # A RUNNING Run may belong to another process. Keep CANCELLING durable;
  # its lease owner confirms cancellation after execution stops.
  return updated
 async def pause(self,tenant_id:str,run_id:str,*,checkpoint_ref:str,reason:str="human intervention requested")->RunSnapshot:
  if not checkpoint_ref.strip():raise ValueError("pause requires a durable checkpoint")
  old=await self.store.get(tenant_id,run_id)
  if old is None:raise KeyError("Run not found")
  if old.status!=RunStatus.RUNNING:raise ValueError("only a running Run can be paused")
  updated=await self.store.transition(RunSnapshot(
   tenant_id,run_id,RunStatus.PAUSED,old.version,
   checkpoint_ref=checkpoint_ref,last_error=reason[:500],
  ),expected_version=old.version)
  await self.tools.cancel_run(run_id,tenant_id=tenant_id)
  async with self._lock: task=self._tasks.get((tenant_id,run_id))
  if task is not None:task.cancel()
  return updated
 async def request_resume(self,tenant_id:str,run_id:str)->RunSnapshot:
  old=await self.store.get(tenant_id,run_id)
  if old is None:raise KeyError("Run not found")
  if old.status!=RunStatus.PAUSED:raise ValueError("only a paused Run can be resumed")
  if not old.checkpoint_ref:raise ValueError("paused Run has no durable checkpoint; resume refused")
  return await self.store.transition(RunSnapshot(
   tenant_id,run_id,RunStatus.INTERRUPTED,old.version,
   checkpoint_ref=old.checkpoint_ref,last_error="human resume requested",
  ),expected_version=old.version)
 async def mark_interrupted(self,tenant_id:str,run_id:str,*,checkpoint_ref:str|None,reason:str)->RunSnapshot:
  old=await self.store.get(tenant_id,run_id)
  if old is None: raise KeyError("Run not found")
  updated=await self.store.transition(RunSnapshot(tenant_id,run_id,RunStatus.INTERRUPTED,old.version,checkpoint_ref,reason[:500]),expected_version=old.version)
  if self.token_service is not None: await self.token_service.revoke_run(tenant_id=tenant_id,run_id=run_id)
  return updated
 async def recover(self,*,checkpoint_loader:Any,resume:Any,limit:int=100,worker_id:str|None=None,lease_seconds:int=60,tenant_id:str|None=None)->list[str]:
  resumed=[]
  claim=getattr(self.store,"claim_recoverable",None)
  rows=await claim(worker_id,tenant_id=tenant_id,limit=limit,lease_seconds=lease_seconds) if worker_id and claim else await self.store.recoverable(limit)
  release=getattr(self.store,"release_lease",None)
  for row in rows:
   running=None
   try:
    # Human approval pauses are deliberate and must never be auto-resumed.
    if row.status==RunStatus.PAUSED: continue
    # A claimed RUNNING row has an expired worker lease and is now safe to interrupt.
    if row.status==RunStatus.RUNNING:
     row=await self.store.transition(RunSnapshot(row.tenant_id,row.run_id,RunStatus.INTERRUPTED,row.version,
      checkpoint_ref=row.checkpoint_ref,last_error="previous worker lease expired"),expected_version=row.version)
     if self.token_service is not None:
      await self.token_service.revoke_run(tenant_id=row.tenant_id,run_id=row.run_id)
    # Interrupted execution without a checkpoint cannot be resumed safely.
    if row.status==RunStatus.INTERRUPTED and not row.checkpoint_ref and row.last_error!="previous worker lease expired":
     await self.finish(row.tenant_id,row.run_id,RunStatus.FAILED,
      error="interrupted Run has no checkpoint; automatic recovery refused")
     continue
    if row.checkpoint_ref: await checkpoint_loader(row)
    running=await self.store.transition(RunSnapshot(row.tenant_id,row.run_id,RunStatus.RUNNING,row.version,
     checkpoint_ref=row.checkpoint_ref),expected_version=row.version)
    await resume(running)
    resumed.append(row.run_id)
   except Exception as exc:
    if isinstance(exc,WorkerLeaseLost): continue
    latest=await self.store.get(row.tenant_id,row.run_id)
    # Do not fail a Run if another worker won the optimistic claim race.
    owns_running=running is not None and latest is not None and latest.status==RunStatus.RUNNING and latest.version==running.version
    checkpoint_invalid=running is None and latest is not None and latest.status==RunStatus.INTERRUPTED
    if owns_running or checkpoint_invalid:
     await self.finish(row.tenant_id,row.run_id,RunStatus.FAILED,
      checkpoint_ref=row.checkpoint_ref,error=f"recovery failed: {str(exc)[:400]}")
   finally:
    if worker_id and release is not None:
     await release(row.tenant_id,row.run_id,worker_id)
  return resumed
 async def run_with_timeout(self,tenant_id:str,run_id:str,operation:Any,*,timeout:float)->Any:
  if isinstance(timeout,bool) or timeout<=0: raise ValueError("timeout must be positive")
  task=asyncio.create_task(operation)
  async with self._lock: self._tasks[(tenant_id,run_id)]=task
  try:
   return await asyncio.wait_for(task,timeout)
  except asyncio.TimeoutError:
   # wait_for has cancelled and joined the operation. Also stop registered
   # tool calls before publishing the terminal state to observers.
   await self.tools.cancel_run(run_id,tenant_id=tenant_id)
   latest=await self.store.get(tenant_id,run_id)
   if latest is not None and latest.status in {RunStatus.RUNNING,RunStatus.CANCELLING}:
    await self.finish(
     tenant_id,run_id,RunStatus.TIMED_OUT,
     error="Run execution timed out",
    )
   raise
  except asyncio.CancelledError:
   # Explicit Run cancellation first persists CANCELLING, then cancels this
   # task. Finalize here so callers never observe a permanently CANCELLING Run.
   await self.tools.cancel_run(run_id,tenant_id=tenant_id)
   latest=await self.store.get(tenant_id,run_id)
   if latest is not None and latest.status==RunStatus.RUNNING:
    latest=await self.store.transition(
     RunSnapshot(
      tenant_id,run_id,RunStatus.CANCELLING,latest.version,
      checkpoint_ref=latest.checkpoint_ref,
     ),
     expected_version=latest.version,
    )
   if latest is not None and latest.status==RunStatus.CANCELLING:
    await self.finish(tenant_id,run_id,RunStatus.CANCELLED)
   raise
  finally:
   async with self._lock:
    self._tasks.pop((tenant_id,run_id),None)
 async def on_client_disconnect(self,tenant_id:str,run_id:str,*,policy:str="continue")->None:
  if policy=="cancel": await self.cancel(tenant_id,run_id)
  elif policy!="continue": raise ValueError("disconnect policy must be continue or cancel")
