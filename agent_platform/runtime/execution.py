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
  intervention_applier:Any|None=None,event_broker:Any|None=None)->None:
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
  self.event_broker=event_broker\n  self.intervention_store=intervention_store
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

 async def _publish_event(self,tenant_id:str,run_id:str,event_type:str,**data:Any)->None:
  if self.event_broker is None:return
  await self.event_broker.publish(
   tenant_id=tenant_id,run_id=run_id,event_type=event_type,data=data,
  )
 async def _invoke_graph(self,payload:Any,config:Any,*,tenant_id:str,run_id:str,session:Any)->Any:
  from agent_platform.streaming.context import token_callback_context
  async def emit_token(token:str)->None:
   await self._publish_event(tenant_id,run_id,"TOKEN_STREAM",token=token)
  await self._publish_event(tenant_id,run_id,"RUN_START")
  stream_events=getattr(self.graph,"astream_events",None)
  if self.event_broker is not None and callable(stream_events):
   latest=None
   try:
    with token_callback_context(emit_token):
     async for item in stream_events(payload,config=config,version="v2"):
      if not isinstance(item,dict):continue
      kind=item.get("event");name=str(item.get("name") or "step")
      data=item.get("data") if isinstance(item.get("data"),dict) else {}
      if kind=="on_chat_model_start":
       await self._publish_event(tenant_id,run_id,"LLM_REQUEST",model=name)
      elif kind=="on_chat_model_stream":
       chunk=data.get("chunk")
       token=getattr(chunk,"content",None)
       if isinstance(token,str) and token:
        await emit_token(token)
       elif isinstance(token,list):
        for part in token:
         text=part.get("text") if isinstance(part,dict) else None
         if isinstance(text,str) and text:await emit_token(text)
      elif kind=="on_chat_model_end":
       await self._publish_event(tenant_id,run_id,"LLM_RESPONSE",model=name)
      elif kind=="on_tool_start":
       await self._publish_event(
        tenant_id,run_id,"TOOL_CALL",tool=name,
        input=data.get("input") if isinstance(data.get("input"),(dict,str,list)) else None,
       )
      elif kind=="on_tool_end":
       output=data.get("output")
       await self._publish_event(
        tenant_id,run_id,"TOOL_OUTPUT",tool=name,
        output=str(output)[:12000] if output is not None else None,
       )
      elif kind=="on_chain_start":
       await self._publish_event(tenant_id,run_id,"STEP_START",step=name)
      elif kind=="on_chain_end":
       output=data.get("output")
       if isinstance(output,dict):
        latest=output
        if session is not None:
         await self.session_persistence.append_progress(
          tenant_id=tenant_id,session_id=session["session_id"],run_id=run_id,
          actor_id=session["actor_id"],state=output,
         )
       await self._publish_event(tenant_id,run_id,"STEP_FINISH",step=name)
      elif kind=="on_chain_error" or kind=="on_tool_error" or kind=="on_chat_model_error":
       error=data.get("error")
       await self._publish_event(
        tenant_id,run_id,"ERROR",step=name,
        message=str(error or "execution failed")[:1000],
       )
    if latest is None:
     raise RuntimeError("LangGraph event stream completed without a final state")
    if session is not None:
     await self._session_checkpoint(tenant_id,run_id,config,session)
    return latest
   except Exception as exc:
    await self._publish_event(
     tenant_id,run_id,"ERROR",message=str(exc)[:1000],
    )
    raise
  stream=getattr(self.graph,"astream",None)
  if session is not None and callable(stream):
   latest=None
   with token_callback_context(emit_token if self.event_broker is not None else None):
    async for state in stream(payload,config=config,stream_mode="values"):
     if isinstance(state,dict):
      await self.session_persistence.append_progress(
       tenant_id=tenant_id,session_id=session["session_id"],run_id=run_id,
       actor_id=session["actor_id"],state=state,
      )
     await self._session_checkpoint(tenant_id,run_id,config,session)
     latest=state
   if latest is None:raise RuntimeError("LangGraph stream completed without a state update")
   return latest
  with token_callback_context(emit_token if self.event_broker is not None else None):
   return await self.graph.ainvoke(payload,config=config)

