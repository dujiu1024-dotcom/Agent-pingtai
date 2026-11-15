"""Connect Run lifecycle management to real LangGraph durable execution."""
from __future__ import annotations
import asyncio
from typing import Any, Callable
from agent_platform.runtime.lifecycle import RunLifecycleManager,RunSnapshot,RunStatus

class LangGraphRunExecutor:
 """Executes a compiled LangGraph using a stable tenant/Run checkpoint thread.

 The graph must be compiled with a durable LangGraph checkpointer, for example
 the platform postgres_checkpointer context manager. A checkpoint is addressed
 by tenant + Run thread; its checkpoint ID is recorded on the platform Run.
 """
 def __init__(self,*,graph:Any,lifecycle:RunLifecycleManager,timeout_seconds:float=900)->None:
  if timeout_seconds<=0:raise ValueError("timeout_seconds must be positive")
  self.graph=graph;self.lifecycle=lifecycle;self.timeout_seconds=timeout_seconds
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
 async def _finish(self,tenant_id:str,run_id:str,status:RunStatus,*,checkpoint_ref:str|None=None,error:str|None=None)->RunSnapshot|None:
  old=await self.lifecycle.store.get(tenant_id,run_id)
  if old is None or old.status!=RunStatus.RUNNING:return old
  return await self.lifecycle.finish(tenant_id,run_id,status,checkpoint_ref=checkpoint_ref,error=error)
 async def execute(self,*,tenant_id:str,run_id:str,input:Any,timeout_seconds:float|None=None)->Any:
  old=await self.lifecycle.store.get(tenant_id,run_id)
  if old is None:raise KeyError("Run not found")
  if old.status not in {RunStatus.QUEUED,RunStatus.INTERRUPTED}:
   raise ValueError(f"Run cannot start from {old.status.value}")
  await self.lifecycle.start(tenant_id,run_id)
  config=self._config(tenant_id,run_id)
  try:
   result=await self.lifecycle.run_with_timeout(tenant_id,run_id,
    self.graph.ainvoke(input,config=config),timeout=timeout_seconds if timeout_seconds is not None else self.timeout_seconds)
  except asyncio.TimeoutError:raise
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
 async def recover(self,*,load_initial_input:Callable[[RunSnapshot],Any],limit:int=100)->list[str]:
  async def verify_checkpoint(row:RunSnapshot)->None:
   actual=await self._checkpoint_ref(row.tenant_id,row.run_id)
   if actual is None or actual!=row.checkpoint_ref:
    raise RuntimeError("LangGraph checkpoint is missing or does not match the Run checkpoint reference")
  async def resume(row:RunSnapshot)->None:
   payload=None if row.checkpoint_ref else load_initial_input(row)
   if hasattr(payload,"__await__"):payload=await payload
   try:
    await self.lifecycle.run_with_timeout(row.tenant_id,row.run_id,
     self.graph.ainvoke(payload,config=self._config(row.tenant_id,row.run_id)),timeout=self.timeout_seconds)
   except asyncio.TimeoutError:raise
   ref=await self._checkpoint_ref(row.tenant_id,row.run_id)
   await self._finish(row.tenant_id,row.run_id,RunStatus.SUCCEEDED,checkpoint_ref=ref)
  return await self.lifecycle.recover(checkpoint_loader=verify_checkpoint,resume=resume,limit=limit)
