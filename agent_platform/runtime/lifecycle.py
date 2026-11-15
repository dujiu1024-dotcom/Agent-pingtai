"""Run state transitions and cancellation propagation around Harness/LangGraph."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import datetime,timezone
from enum import Enum
from typing import Any,Protocol

def _now() -> datetime: return datetime.now(timezone.utc)
class RunStatus(str,Enum):
    QUEUED="queued"; RUNNING="running"; PAUSED="paused"; CANCELLING="cancelling"
    SUCCEEDED="succeeded"; FAILED="failed"; CANCELLED="cancelled"; TIMED_OUT="timed_out"; INTERRUPTED="interrupted"
TERMINAL={RunStatus.SUCCEEDED,RunStatus.FAILED,RunStatus.CANCELLED,RunStatus.TIMED_OUT}
_ALLOWED={
 RunStatus.QUEUED:{RunStatus.RUNNING,RunStatus.CANCELLED},
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
 async def recoverable(self,limit:int=100)->list[RunSnapshot]:
  if limit<1: raise ValueError("limit must be positive")
  async with self._lock: return [r for r in self._rows.values() if r.status in {RunStatus.QUEUED,RunStatus.INTERRUPTED,RunStatus.PAUSED}][:limit]
class ToolCallRegistry:
 """Tracks in-flight calls by Run and propagates cancellation to asyncio tasks."""
 def __init__(self)->None: self._calls={}; self._lock=asyncio.Lock()
 async def run(self,run_id:str,call_id:str,awaitable:Any,*,timeout:float|None=None)->Any:
  key=(run_id,call_id)
  async with self._lock:
   if key in self._calls: raise ValueError("duplicate active tool call id")
   task=asyncio.create_task(awaitable); self._calls[key]=task
  try: return await asyncio.wait_for(task,timeout) if timeout is not None else await task
  finally:
   async with self._lock: self._calls.pop(key,None)
 async def cancel_run(self,run_id:str)->int:
  async with self._lock:
   tasks=[t for (rid,_),t in self._calls.items() if rid==run_id]
   for task in tasks: task.cancel()
  if tasks: await asyncio.gather(*tasks,return_exceptions=True)
  return len(tasks)
class RunLifecycleManager:
 def __init__(self,store:RunStateStore,*,tools:ToolCallRegistry|None=None)->None:
  self.store=store; self.tools=tools or ToolCallRegistry(); self._tasks={}; self._lock=asyncio.Lock()
 async def start(self,tenant_id:str,run_id:str)->RunSnapshot:
  old=await self.store.get(tenant_id,run_id)
  if old is None: raise KeyError("Run not found")
  return await self.store.transition(RunSnapshot(tenant_id,run_id,RunStatus.RUNNING,old.version),expected_version=old.version)
 async def cancel(self,tenant_id:str,run_id:str)->RunSnapshot:
  old=await self.store.get(tenant_id,run_id)
  if old is None: raise KeyError("Run not found")
  if old.status in TERMINAL: return old
  target=RunStatus.CANCELLED if old.status in {RunStatus.QUEUED,RunStatus.PAUSED,RunStatus.INTERRUPTED} else RunStatus.CANCELLING
  updated=await self.store.transition(RunSnapshot(tenant_id,run_id,target,old.version),expected_version=old.version)
  await self.tools.cancel_run(run_id)
  async with self._lock: task=self._tasks.get((tenant_id,run_id))
  if task is not None: task.cancel()
  if updated.status==RunStatus.CANCELLING:
   latest=await self.store.get(tenant_id,run_id)
   if latest and latest.status==RunStatus.CANCELLING:
    updated=await self.store.transition(RunSnapshot(tenant_id,run_id,RunStatus.CANCELLED,latest.version),expected_version=latest.version)
  return updated
 async def mark_interrupted(self,tenant_id:str,run_id:str,*,checkpoint_ref:str|None,reason:str)->RunSnapshot:
  old=await self.store.get(tenant_id,run_id)
  if old is None: raise KeyError("Run not found")
  return await self.store.transition(RunSnapshot(tenant_id,run_id,RunStatus.INTERRUPTED,old.version,checkpoint_ref,reason[:500]),expected_version=old.version)
 async def recover(self,*,checkpoint_loader:Any,resume:Any,limit:int=100)->list[str]:
  resumed=[]
  for row in await self.store.recoverable(limit):
   if row.status==RunStatus.PAUSED or not row.checkpoint_ref: continue
   await checkpoint_loader(row); await resume(row); resumed.append(row.run_id)
  return resumed
 async def run_with_timeout(self,tenant_id:str,run_id:str,operation:Any,*,timeout:float)->Any:
  if timeout<=0: raise ValueError("timeout must be positive")
  task=asyncio.create_task(operation)
  async with self._lock: self._tasks[(tenant_id,run_id)]=task
  try: return await asyncio.wait_for(task,timeout)
  except asyncio.TimeoutError:
   old=await self.store.get(tenant_id,run_id)
   if old and old.status==RunStatus.RUNNING:
    await self.store.transition(RunSnapshot(tenant_id,run_id,RunStatus.TIMED_OUT,old.version,last_error="Run execution timed out"),expected_version=old.version)
   await self.tools.cancel_run(run_id); raise
  finally:
   async with self._lock: self._tasks.pop((tenant_id,run_id),None)
 async def on_client_disconnect(self,tenant_id:str,run_id:str,*,policy:str="continue")->None:
  if policy=="cancel": await self.cancel(tenant_id,run_id)
  elif policy!="continue": raise ValueError("disconnect policy must be continue or cancel")
