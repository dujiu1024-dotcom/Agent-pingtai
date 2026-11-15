"""Connect Run lifecycle management to LangGraph execution and worker recovery."""
from __future__ import annotations
import asyncio
from typing import Any, Callable
from uuid import uuid4
from agent_platform.runtime.lifecycle import RunLifecycleManager, RunSnapshot, RunStatus, WorkerLeaseLost

class LangGraphRunExecutor:
 """Execute a compiled LangGraph with durable checkpoints and a renewable worker lease."""
 def __init__(self,*,graph:Any,lifecycle:RunLifecycleManager,timeout_seconds:float=900,
  worker_id:str|None=None,lease_seconds:int=60)->None:
  if timeout_seconds<=0 or lease_seconds<5:raise ValueError("invalid Run timeout or lease duration")
  if getattr(graph,"checkpointer",None) is None:raise ValueError("LangGraph must be compiled with a durable Checkpointer")
  self.graph=graph;self.lifecycle=lifecycle;self.timeout_seconds=timeout_seconds
  self.worker_id=worker_id or uuid4().hex;self.lease_seconds=lease_seconds
 @staticmethod
 def _config(tenant_id:str,run_id:str)->dict[str,Any]:
  return {"configurable":{"thread_id":f"{tenant_id}:{run_id}"}}
 async def _checkpoint_ref(self,tenant_id:str,run_id:str)->str|None:
  getter=getattr(self.graph,"aget_state",None)
  if getter is None:return None
  state=await getter(self._config(tenant_id,run_id))
  configurable=(getattr(state,"config",None) or {}).get("configurable",{})
  value=configurable.get("checkpoint_id")
  return str(value) if value else None
 async def _run_leased(self,tenant_id:str,run_id:str,awaitable:Any,*,timeout:float,already_claimed:bool=False)->Any:
  store=self.lifecycle.store
  acquire=getattr(store,"acquire_lease",None)
  heartbeat=getattr(store,"heartbeat",None)
  release=getattr(store,"release_lease",None)
  if timeout<=0:
   close=getattr(awaitable,"close",None)
   if close is not None:close()
   raise ValueError("Run timeout must be positive")
  acquired=False
  if not already_claimed and acquire is not None:
   acquired=await acquire(tenant_id,run_id,self.worker_id,lease_seconds=self.lease_seconds)
   if not acquired:
    close=getattr(awaitable,"close",None)
    if close is not None:close()
    raise WorkerLeaseLost("could not acquire Run worker lease")
  task=asyncio.create_task(self.lifecycle.run_with_timeout(tenant_id,run_id,awaitable,timeout=timeout))
  beat_task=None
  if heartbeat is not None and (already_claimed or acquired):
   async def beat()->None:
    while True:
     await asyncio.sleep(max(1,self.lease_seconds/3))
     if not await heartbeat(tenant_id,run_id,self.worker_id,lease_seconds=self.lease_seconds):
      current=await store.get(tenant_id,run_id)
      if current is not None and current.status in {RunStatus.SUCCEEDED,RunStatus.FAILED,RunStatus.CANCELLED,RunStatus.TIMED_OUT}:
       return
      task.cancel()
      raise WorkerLeaseLost("Run worker lease was lost")
   beat_task=asyncio.create_task(beat())
  try:
   if beat_task is None:return await task
   done,_=await asyncio.wait({task,beat_task},return_when=asyncio.FIRST_COMPLETED)
   if beat_task in done:
    try:await beat_task
    except Exception:
     task.cancel()
     await asyncio.gather(task,return_exceptions=True)
     raise
   return await task
  finally:
   if beat_task is not None:
    beat_task.cancel();await asyncio.gather(beat_task,return_exceptions=True)
   if not already_claimed and acquired and release is not None:
    await release(tenant_id,run_id,self.worker_id)
 async def _finish(self,tenant_id:str,run_id:str,status:RunStatus,*,checkpoint_ref:str|None=None,error:str|None=None)->RunSnapshot|None:
  old=await self.lifecycle.store.get(tenant_id,run_id)
  if old is None or old.status!=RunStatus.RUNNING:return old
  return await self.lifecycle.finish(tenant_id,run_id,status,checkpoint_ref=checkpoint_ref,error=error)
 async def execute(self,*,tenant_id:str,run_id:str,input:Any,timeout_seconds:float|None=None)->Any:
  old=await self.lifecycle.store.get(tenant_id,run_id)
  if old is None:raise KeyError("Run not found")
  if old.status!=RunStatus.QUEUED:raise ValueError(f"Run cannot start from {old.status.value}; use recovery for interrupted Runs")
  store=self.lifecycle.store
  acquire=getattr(store,"acquire_lease",None)
  release=getattr(store,"release_lease",None)
  acquired=False
  # Claim the worker lease before transitioning QUEUED -> RUNNING. Otherwise
  # a failed lease claim strands the Run in RUNNING with no executor owning it.
  if acquire is not None:
   acquired=await acquire(tenant_id,run_id,self.worker_id,lease_seconds=self.lease_seconds)
   if not acquired:
    raise WorkerLeaseLost("could not acquire Run worker lease")
  try:
   await self.lifecycle.start(tenant_id,run_id)
   config=self._config(tenant_id,run_id)
   try:
    result=await self._run_leased(tenant_id,run_id,self.graph.ainvoke(input,config=config),
     timeout=timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
     already_claimed=acquired)
   except asyncio.TimeoutError:raise
   except WorkerLeaseLost:raise
   except asyncio.CancelledError:
    current=await self.lifecycle.store.get(tenant_id,run_id)
    if current and current.status==RunStatus.RUNNING:
     ref=await self._checkpoint_ref(tenant_id,run_id)
     await self.lifecycle.mark_interrupted(tenant_id,run_id,checkpoint_ref=ref,reason="execution task cancelled")
    raise
   except Exception as exc:
    ref=await self._checkpoint_ref(tenant_id,run_id)
    await self._finish(tenant_id,run_id,RunStatus.FAILED,checkpoint_ref=ref,error=str(exc)[:500])
    raise
   ref=await self._checkpoint_ref(tenant_id,run_id)
   await self._finish(tenant_id,run_id,RunStatus.SUCCEEDED,checkpoint_ref=ref)
   return result
  finally:
   if acquired and release is not None:
    await release(tenant_id,run_id,self.worker_id)
 async def recover(self,*,load_initial_input:Callable[[RunSnapshot],Any],limit:int=100)->list[str]:
  async def verify_checkpoint(row:RunSnapshot)->None:
   actual=await self._checkpoint_ref(row.tenant_id,row.run_id)
   if actual is None or actual!=row.checkpoint_ref:
    raise RuntimeError("LangGraph checkpoint is missing or does not match the Run checkpoint reference")
  async def resume(row:RunSnapshot)->None:
   payload=None if row.checkpoint_ref else load_initial_input(row)
   if hasattr(payload,"__await__"):payload=await payload
   ref_input=self.graph.ainvoke(payload,config=self._config(row.tenant_id,row.run_id))
   try:
    await self._run_leased(row.tenant_id,row.run_id,ref_input,timeout=self.timeout_seconds,already_claimed=True)
   except asyncio.TimeoutError:raise
   ref=await self._checkpoint_ref(row.tenant_id,row.run_id)
   await self._finish(row.tenant_id,row.run_id,RunStatus.SUCCEEDED,checkpoint_ref=ref)
  return await self.lifecycle.recover(checkpoint_loader=verify_checkpoint,resume=resume,limit=limit,
   worker_id=self.worker_id,lease_seconds=self.lease_seconds)
