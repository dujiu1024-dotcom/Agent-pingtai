"""Connect Run lifecycle management to LangGraph execution and worker recovery."""
from __future__ import annotations

import asyncio
from typing import Any, Callable
from uuid import uuid4

from agent_platform.observability.trace_spans import trace_span
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
  required_tools:tuple[str,...]=(),run_context_service:Any|None=None,session_repository:Any|None=None,intervention_store:Any|None=None,
  intervention_applier:Any|None=None)->None:
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
  self.run_context_service=run_context_service
  self.session_repository=session_repository
  self.session_persistence=None
  if session_repository is not None:
   from agent_platform.runtime.session_persistence import SessionRunPersistence
   self.session_persistence=SessionRunPersistence(session_repository)
  self.intervention_store=intervention_store
  self.intervention_applier=intervention_applier
  if intervention_store is not None and intervention_applier is None:
   raise ValueError("intervention_applier is required with intervention_store")
 @staticmethod
 def _config(tenant_id:str,run_id:str)->dict[str,Any]:
  return {"configurable":{
   "thread_id":f"{tenant_id}:{run_id}",
   "platform_tenant_id":tenant_id,
   "platform_run_id":run_id,
  }}
 async def _run_config(self,tenant_id:str,run_id:str,*,session_history:Any|None=None)->dict[str,Any]:
  config=self._config(tenant_id,run_id)
  if session_history is not None:
   config["configurable"]["platform_session_history"]=list(session_history)
  if self.registry_snapshot_service is not None:
   snapshot=await self.registry_snapshot_service.get(tenant_id=tenant_id,run_id=run_id)
   config["configurable"]["platform_registry_snapshot"]=[dict(entry) for entry in snapshot.entries]
   config["configurable"]["platform_registry_snapshot_digest"]=snapshot.digest
  if self.run_context_service is not None:
   config["configurable"]["platform_run_context"]=await self.run_context_service.rebuild(
    tenant_id=tenant_id,run_id=run_id,
   )
  return config
 async def _session_begin(self,tenant_id:str,run_id:str,payload:Any,session_context:Any)->tuple[dict[str,Any]|None,Any|None]:
  if session_context is None:return None,None
  if self.session_persistence is None:
   raise ValueError("session_context requires a configured session_repository")
  if not isinstance(session_context,dict):raise ValueError("session_context must be an object")
  session_id=session_context.get("session_id");actor_id=session_context.get("actor_id")
  if not isinstance(session_id,str) or not session_id.strip() or not isinstance(actor_id,str) or not actor_id.strip():
   raise ValueError("session_context requires session_id and actor_id")
  history=await self.session_persistence.begin_run(
   tenant_id=tenant_id,session_id=session_id,run_id=run_id,actor_id=actor_id,payload=payload,
  )
  return {"session_id":session_id,"actor_id":actor_id},history

 async def _invoke_graph(self,payload:Any,config:Any,*,tenant_id:str,run_id:str,session:Any)->Any:
  stream=getattr(self.graph,"astream",None)
  if session is None or not callable(stream):
   return await self.graph.ainvoke(payload,config=config)
  latest=None
  async for state in stream(payload,config=config,stream_mode="values"):
   if isinstance(state,dict):
    await self.session_persistence.append_progress(
     tenant_id=tenant_id,session_id=session["session_id"],run_id=run_id,
     actor_id=session["actor_id"],state=state,
    )
   # LangGraph persists its own node checkpoint before yielding the values update.
   await self._session_checkpoint(tenant_id,run_id,config,session)
   latest=state
  if latest is None:
   raise RuntimeError("LangGraph stream completed without a state update")
  return latest

 async def _session_checkpoint(self,tenant_id:str,run_id:str,config:Any,session:Any)->None:
  if session is None or self.session_persistence is None:return
  try:
   await self.session_persistence.checkpoint(
    graph=self.graph,config=config,tenant_id=tenant_id,
    session_id=session["session_id"],run_id=run_id,actor_id=session["actor_id"],
   )
  except Exception:
   # The LangGraph checkpointer and Run checkpoint_ref remain authoritative;
   # a mirror failure must not hide the original execution outcome.
   raise

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
 async def execute(self,*,tenant_id:str,run_id:str,input:Any,timeout_seconds:float|None=None,session_context:Any|None=None)->Any:
  old=await self.lifecycle.store.get(tenant_id,run_id)
  if old is None:raise KeyError("Run not found")
  if old.status!=RunStatus.QUEUED:raise ValueError(f"Run cannot start from {old.status.value}; use recovery for interrupted Runs")
  session,session_history=await self._session_begin(tenant_id,run_id,input,session_context)
  if self.run_context_service is not None:
   if not isinstance(input,dict):
    raise ValueError("Run context assembly requires an object input")
   await self.run_context_service.ensure_initial(
    tenant_id=tenant_id,run_id=run_id,initial_context=input,
   )
  config=await self._run_config(tenant_id,run_id,session_history=session_history)
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
    with trace_span("run.execute", component="run", tenant_id=tenant_id, run_id=run_id):
     result=await self._run_leased(tenant_id,run_id,self._invoke_graph(input,config,tenant_id=tenant_id,run_id=run_id,session=session),
      timeout=timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
      already_claimed=acquired)
   except asyncio.TimeoutError:raise
   except WorkerLeaseLost:
    current=await self.lifecycle.store.get(tenant_id,run_id)
    if current and current.status==RunStatus.CANCELLING:
     await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
    # A lease transfer is not an execution failure; the new owner recovers it.
    raise
   except asyncio.CancelledError:
    current=await self.lifecycle.store.get(tenant_id,run_id)
    if current and current.status==RunStatus.CANCELLING:
     await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
    elif current and current.status==RunStatus.RUNNING:
     ref=await self._checkpoint_ref(tenant_id,run_id)
     if session is not None:
      await self._session_checkpoint(tenant_id,run_id,config,session)
     await self.lifecycle.mark_interrupted(tenant_id,run_id,checkpoint_ref=ref,reason="execution task cancelled")
    # A deliberate human pause has already persisted PAUSED + checkpoint_ref.
    raise
   except Exception as exc:
    ref=await self._checkpoint_ref(tenant_id,run_id)
    if session is not None:
     await self._session_checkpoint(tenant_id,run_id,config,session)
    await self._finish(tenant_id,run_id,RunStatus.FAILED,checkpoint_ref=ref,error=str(exc)[:500])
    raise
   ref=await self._checkpoint_ref(tenant_id,run_id)
   latest=await self.lifecycle.store.get(tenant_id,run_id)
   if latest is not None and latest.status==RunStatus.CANCELLING:
    await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
    return None
   if session is not None:
    await self.session_persistence.append_assistant(
     tenant_id=tenant_id,session_id=session["session_id"],run_id=run_id,
     actor_id=session["actor_id"],result=result,
    )
    await self._session_checkpoint(tenant_id,run_id,config,session)
   await self._finish(tenant_id,run_id,RunStatus.SUCCEEDED,checkpoint_ref=ref)
   return result
  finally:
   try:
    if runtime_prepared and self.registry_runtime_provider is not None:
     await self.registry_runtime_provider.release(tenant_id,run_id)
   finally:
    if acquired and release is not None:
     await release(tenant_id,run_id,self.worker_id)
 async def resume_claimed(self,*,snapshot:RunSnapshot,input:Any,session_context:Any|None=None)->Any:
  """Resume one Run already claimed by this worker's durable queue poll."""
  tenant_id=snapshot.tenant_id;run_id=snapshot.run_id
  owns_lease=getattr(self.lifecycle.store,"owns_lease",None)
  if owns_lease is not None and not await owns_lease(
   tenant_id,run_id,self.worker_id,
  ):
   raise WorkerLeaseLost("Run worker lease was lost before resume")
  current=await self.lifecycle.store.get(tenant_id,run_id)
  if current is None:raise KeyError("Run not found")
  if current.status==RunStatus.CANCELLING:
   await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
   return None
  if current.status==RunStatus.QUEUED:
   return await self.execute(tenant_id=tenant_id,run_id=run_id,input=input,session_context=session_context)
  if current.status==RunStatus.RUNNING:
   current=await self.lifecycle.store.transition(
    RunSnapshot(tenant_id,run_id,RunStatus.INTERRUPTED,current.version,
     current.checkpoint_ref,"previous worker lease expired"),
    expected_version=current.version,
   )
  if current.status!=RunStatus.INTERRUPTED:
   raise ValueError(f"claimed Run cannot resume from {current.status.value}")
  session,session_history=await self._session_begin(tenant_id,run_id,input,session_context)
  if current.checkpoint_ref:
   actual=await self._checkpoint_ref(tenant_id,run_id)
   if actual!=current.checkpoint_ref:
    await self.lifecycle.store.transition(
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
   with trace_span("run.resume", component="run", tenant_id=tenant_id, run_id=run_id):
    recovery_config=await self._run_config(tenant_id,run_id,session_history=session_history)
   result=await self._run_leased(
     tenant_id,run_id,
     self._invoke_graph(payload,recovery_config,tenant_id=tenant_id,run_id=run_id,session=session),
     timeout=self.timeout_seconds,already_claimed=True,
    )
   ref=await self._checkpoint_ref(tenant_id,run_id)
   latest=await self.lifecycle.store.get(tenant_id,run_id)
   if latest is not None and latest.status==RunStatus.CANCELLING:
    await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
    return None
   if session is not None:
    await self.session_persistence.append_assistant(
     tenant_id=tenant_id,session_id=session["session_id"],run_id=run_id,
     actor_id=session["actor_id"],result=result,
    )
    await self._session_checkpoint(tenant_id,run_id,recovery_config,session)
   await self._finish(tenant_id,run_id,RunStatus.SUCCEEDED,checkpoint_ref=ref)
   return result
  except asyncio.TimeoutError:
   raise
  except WorkerLeaseLost:
   latest=await self.lifecycle.store.get(tenant_id,run_id)
   if latest and latest.status==RunStatus.CANCELLING:
    await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
   # A lease transfer is not an execution failure. Leave RUNNING untouched
   # so the worker that owns the new lease can recover it safely.
   raise
  except asyncio.CancelledError:
   latest=await self.lifecycle.store.get(tenant_id,run_id)
   if latest and latest.status==RunStatus.CANCELLING:
    await self.lifecycle.finish(tenant_id,run_id,RunStatus.CANCELLED)
   elif latest and latest.status==RunStatus.RUNNING:
    ref=await self._checkpoint_ref(tenant_id,run_id)
    if session is not None:
     await self._session_checkpoint(
      tenant_id,run_id,await self._run_config(tenant_id,run_id,session_history=session_history),session,
     )
    await self.lifecycle.mark_interrupted(
     tenant_id,run_id,checkpoint_ref=ref,reason="recovery execution cancelled")
   raise
  except Exception as exc:
   ref=await self._checkpoint_ref(tenant_id,run_id)
   if session is not None:
    await self._session_checkpoint(
     tenant_id,run_id,await self._run_config(tenant_id,run_id,session_history=session_history),session,
    )
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

 async def pause(self, *, tenant_id: str, run_id: str, reason: str = "human intervention requested") -> RunSnapshot:
  checkpoint_ref=await self._checkpoint_ref(tenant_id,run_id)
  if not checkpoint_ref:
   raise ValueError("Run has no durable checkpoint; pause refused")
  return await self.lifecycle.pause(
   tenant_id,run_id,checkpoint_ref=checkpoint_ref,reason=reason,
  )
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
    recovery_config=await self._run_config(row.tenant_id,row.run_id)
    if self.intervention_store is not None:
     applier=self.intervention_applier
     if applier is None:
      raise RuntimeError("human intervention applier is not configured")
     async def apply_intervention(item:Any)->None:
      await applier(
       graph=self.graph,config=recovery_config,intervention=item,
      )
     await self.intervention_store.apply_pending(
      tenant_id=row.tenant_id,run_id=row.run_id,
      worker_id=self.worker_id,applier=apply_intervention,
     )
    ref_input=self.graph.ainvoke(payload,config=recovery_config)
    try:
     await self._run_leased(row.tenant_id,row.run_id,ref_input,timeout=self.timeout_seconds,already_claimed=True)
    except asyncio.TimeoutError:raise
    except WorkerLeaseLost:
     current=await self.lifecycle.store.get(row.tenant_id,row.run_id)
     if current and current.status==RunStatus.CANCELLING:
      await self.lifecycle.finish(row.tenant_id,row.run_id,RunStatus.CANCELLED)
     raise
    ref=await self._checkpoint_ref(row.tenant_id,row.run_id)
    await self._finish(row.tenant_id,row.run_id,RunStatus.SUCCEEDED,checkpoint_ref=ref)
   finally:
    if runtime_prepared and self.registry_runtime_provider is not None:
     await self.registry_runtime_provider.release(row.tenant_id,row.run_id)
  return await self.lifecycle.recover(checkpoint_loader=verify_checkpoint,resume=resume,limit=limit,
   worker_id=self.worker_id,lease_seconds=self.lease_seconds,tenant_id=tenant_id)
