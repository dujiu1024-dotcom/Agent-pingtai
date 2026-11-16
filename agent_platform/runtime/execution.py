"""Connect Run lifecycle management to LangGraph execution and worker recovery."""
from __future__ import annotations

import asyncio
from typing import Any, Callable
from uuid import uuid4

from agent_platform.runtime.lifecycle import (
 RunLifecycleManager,
 RunSnapshot,
 RunStatus,
 WorkerLeaseLost,
)


class LangGraphRunExecutor:
 """Execute a compiled LangGraph with durable checkpoints and a renewable worker lease."""
 def __init__(self,*,graph:Any,lifecycle:RunLifecycleManager,timeout_seconds:float=900,
  worker_id:str|None=None,lease_seconds:int=60,registry_snapshot_service:Any|None=None,
  registry_runtime_provider:Any|None=None,model_template_id:str|None=None,
  required_tools:tuple[str,...]=())->None:
  if timeout_seconds<=0 or lease_seconds<5:raise ValueError("invalid Run timeout or lease duration")
  if getattr(graph,"checkpointer",None) is None:raise ValueError("LangGraph must be compiled with a durable Checkpointer")
  if registry_runtime_provider is not None and not (model_template_id or "").strip():
   raise ValueError("model_template_id is required with registry_runtime_provider")
  self.graph=graph;self.lifecycle=lifecycle;self.timeout_seconds=timeout_seconds
  self.worker_id=worker_id or uuid4().hex;self.lease_seconds=lease_seconds
  self.registry_snapshot_service=registry_snapshot_service
  self.registry_runtime_provider=registry_runtime_provider
  self.model_template_id=model_template_id
  self.required_tools=tuple(required_tools)
 @staticmethod
 def _config(tenant_id:str,run_id:str)->dict[str,Any]:
  return {"configurable":{"thread_id":f"{tenant_id}:{run_id}"}}
 async def _run_config(self,tenant_id:str,run_id:str)->dict[str,Any]:
  config=self._config(tenant_id,run_id)
  if self.registry_snapshot_service is not None:
   snapshot=await self.registry_snapshot_service.get(tenant_id=tenant_id,run_id=run_id)
   config["configurable"]["platform_registry_snapshot"]=[dict(entry) for entry in snapshot.entries]
   config["configurable"]["platform_registry_snapshot_digest"]=snapshot.digest
  return config
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
   # Cancelling the caller (or losing the lease) must not leave the
   # underlying graph task running after the Run has been marked interrupted.
   if not task.done():
    task.cancel()
    await asyncio.gather(task,return_exceptions=True)
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
  config=await self._run_config(tenant_id,run_id)
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
  runtime_prepared=False
  try:
   await self.lifecycle.start(tenant_id,run_id)
   try:
    if self.registry_runtime_provider is not None:
     await self.registry_runtime_provider.prepare(
      tenant_id=tenant_id,run_id=run_id,
      model_template_id=self.model_template_id or "",
      required_tools=self.required_tools,
     )
     runtime_prepared=True
    result=await self._run_leased(tenant_id,run_id,self.graph.ainvoke(input,config=config),
     timeout=timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
     already_claimed=acquired)
   except asyncio.TimeoutError:raise
   except WorkerLeaseLost:
    current=await self.lifecycle.store.get(tenant_id,run_id)
    if current and current.status==RunStatus.CANCELLING:
     await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
    elif current and current.status==RunStatus.RUNNING:
     ref=await self._checkpoint_ref(tenant_id,run_id)
     await self.lifecycle.mark_interrupted(tenant_id,run_id,checkpoint_ref=ref,reason="worker lease lost")
    raise
   except asyncio.CancelledError:
    current=await self.lifecycle.store.get(tenant_id,run_id)
    if current and current.status==RunStatus.CANCELLING:
     await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
    elif current and current.status==RunStatus.RUNNING:
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
   try:
    if runtime_prepared and self.registry_runtime_provider is not None:
     await self.registry_runtime_provider.release(tenant_id,run_id)
   finally:
    if acquired and release is not None:
     await release(tenant_id,run_id,self.worker_id)
 async def resume_claimed(self,*,snapshot:RunSnapshot,input:Any)->Any:
  """Resume one Run already claimed by this worker's durable queue poll."""
  tenant_id=snapshot.tenant_id;run_id=snapshot.run_id
  current=await self.lifecycle.store.get(tenant_id,run_id)
  if current is None:raise KeyError("Run not found")
  if current.status==RunStatus.CANCELLING:
   await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
   return None
  if current.status==RunStatus.QUEUED:
   return await self.execute(tenant_id=tenant_id,run_id=run_id,input=input)
  if current.status==RunStatus.RUNNING:
   current=await self.lifecycle.store.transition(
    RunSnapshot(tenant_id,run_id,RunStatus.INTERRUPTED,current.version,
     current.checkpoint_ref,"previous worker lease expired"),
    expected_version=current.version,
   )
  if current.status!=RunStatus.INTERRUPTED:
   raise ValueError(f"claimed Run cannot resume from {current.status.value}")
  if current.checkpoint_ref:
   actual=await self._checkpoint_ref(tenant_id,run_id)
   if actual!=current.checkpoint_ref:
    failed=await self.lifecycle.store.transition(
     RunSnapshot(tenant_id,run_id,RunStatus.FAILED,current.version,
      current.checkpoint_ref,"LangGraph checkpoint is missing or does not match the Run reference"),
     expected_version=current.version,
    )
    return None
  elif current.last_error!="previous worker lease expired":
   await self.lifecycle.finish(
    tenant_id,run_id,RunStatus.FAILED,
    error="interrupted Run has no checkpoint; automatic recovery refused",
   )
   return None
  running=await self.lifecycle.store.transition(
   RunSnapshot(tenant_id,run_id,RunStatus.RUNNING,current.version,
    current.checkpoint_ref),expected_version=current.version,
  )
  runtime_prepared=False
  try:
   if self.registry_runtime_provider is not None:
    await self.registry_runtime_provider.prepare(
     tenant_id=tenant_id,run_id=run_id,
     model_template_id=self.model_template_id or "",
     required_tools=self.required_tools,
    )
    runtime_prepared=True
   payload=None if running.checkpoint_ref else input
   result=await self._run_leased(
    tenant_id,run_id,
    self.graph.ainvoke(payload,config=await self._run_config(tenant_id,run_id)),
    timeout=self.timeout_seconds,already_claimed=True,
   )
   ref=await self._checkpoint_ref(tenant_id,run_id)
   await self._finish(tenant_id,run_id,RunStatus.SUCCEEDED,checkpoint_ref=ref)
   return result
  except asyncio.TimeoutError:
   raise
  except WorkerLeaseLost:
   latest=await self.lifecycle.store.get(tenant_id,run_id)
   if latest and latest.status==RunStatus.CANCELLING:
    await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
   elif latest and latest.status==RunStatus.RUNNING:
    ref=await self._checkpoint_ref(tenant_id,run_id)
    await self.lifecycle.mark_interrupted(
     tenant_id,run_id,checkpoint_ref=ref,reason="worker lease lost during recovery")
   raise
  except asyncio.CancelledError:
   latest=await self.lifecycle.store.get(tenant_id,run_id)
   if latest and latest.status==RunStatus.CANCELLING:
    await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
   elif latest and latest.status==RunStatus.RUNNING:
    ref=await self._checkpoint_ref(tenant_id,run_id)
    await self.lifecycle.mark_interrupted(
     tenant_id,run_id,checkpoint_ref=ref,reason="recovery execution cancelled")
   raise
  except Exception as exc:
   ref=await self._checkpoint_ref(tenant_id,run_id)
   await self._finish(tenant_id,run_id,RunStatus.FAILED,
    checkpoint_ref=ref,error=str(exc)[:500])
   raise
  finally:
   try:
    if runtime_prepared and self.registry_runtime_provider is not None:
     await self.registry_runtime_provider.release(tenant_id,run_id)
   finally:
    release=getattr(self.lifecycle.store,"release_lease",None)
    if release is not None:
     await release(tenant_id,run_id,self.worker_id)

 async def recover(self,*,load_initial_input:Callable[[RunSnapshot],Any],limit:int=100,tenant_id:str|None=None)->list[str]:
  async def verify_checkpoint(row:RunSnapshot)->None:
   actual=await self._checkpoint_ref(row.tenant_id,row.run_id)
   if actual is None or actual!=row.checkpoint_ref:
    raise RuntimeError("LangGraph checkpoint is missing or does not match the Run checkpoint reference")
  async def resume(row:RunSnapshot)->None:
   runtime_prepared=False
   if self.registry_runtime_provider is not None:
    await self.registry_runtime_provider.prepare(
     tenant_id=row.tenant_id,run_id=row.run_id,
     model_template_id=self.model_template_id or "",
     required_tools=self.required_tools,
    )
    runtime_prepared=True
   try:
    payload: Any = None if row.checkpoint_ref else load_initial_input(row)
    if hasattr(payload,"__await__"):payload=await payload
    ref_input=self.graph.ainvoke(payload,config=await self._run_config(row.tenant_id,row.run_id))
    try:
     await self._run_leased(row.tenant_id,row.run_id,ref_input,timeout=self.timeout_seconds,already_claimed=True)
    except asyncio.TimeoutError:raise
    except WorkerLeaseLost:
     current=await self.lifecycle.store.get(row.tenant_id,row.run_id)
     if current and current.status==RunStatus.RUNNING:
      ref=await self._checkpoint_ref(row.tenant_id,row.run_id)
      await self.lifecycle.mark_interrupted(
       row.tenant_id,row.run_id,checkpoint_ref=ref,reason="worker lease lost during recovery")
     raise
    ref=await self._checkpoint_ref(row.tenant_id,row.run_id)
    await self._finish(row.tenant_id,row.run_id,RunStatus.SUCCEEDED,checkpoint_ref=ref)
   finally:
    if runtime_prepared and self.registry_runtime_provider is not None:
     await self.registry_runtime_provider.release(row.tenant_id,row.run_id)
  return await self.lifecycle.recover(checkpoint_loader=verify_checkpoint,resume=resume,limit=limit,
   worker_id=self.worker_id,lease_seconds=self.lease_seconds,tenant_id=tenant_id)
