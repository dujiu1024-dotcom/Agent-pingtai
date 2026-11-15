"""Concurrent multi-Run ASGI Gateway dispatcher with endpoint ownership routing."""
from __future__ import annotations
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime,timezone
from typing import Any,Callable,Protocol
from urllib.parse import urlsplit

@dataclass(frozen=True)
class EndpointRecord:
 tenant_id:str; run_id:str; instance_id:str; endpoint_url:str; status:str; created_at:datetime
class EndpointDirectory(Protocol):
 async def register(self,record:EndpointRecord)->None: ...
 async def get(self,tenant_id:str,run_id:str)->EndpointRecord|None: ...
 async def deactivate(self,tenant_id:str,run_id:str)->bool: ...
class InMemoryEndpointDirectory:
 """Test adapter; production deployments must supply a shared Postgres directory."""
 def __init__(self)->None:self._rows={};self._lock=asyncio.Lock()
 async def register(self,row:EndpointRecord)->None:
  async with self._lock:
   key=(row.tenant_id,row.run_id);old=self._rows.get(key)
   if old and old.status=="active":raise ValueError("active endpoint already exists")
   self._rows[key]=row
 async def get(self,tenant_id:str,run_id:str)->EndpointRecord|None:
  async with self._lock:return self._rows.get((tenant_id,run_id))
 async def deactivate(self,tenant_id:str,run_id:str)->bool:
  async with self._lock:
   key=(tenant_id,run_id);old=self._rows.get(key)
   if not old or old.status!="active":return False
   self._rows[key]=EndpointRecord(old.tenant_id,old.run_id,old.instance_id,old.endpoint_url,"inactive",old.created_at)
   return True

class GatewayService:
 """Each Run gets a private MCP ASGI app/session; shared directory enables routing."""
 def __init__(self,*,instance_id:str,public_base_url:str,directory:EndpointDirectory,
  app_factory:Callable[[Any],Any],token_service:Any|None=None)->None:
  if not instance_id.strip():raise ValueError("instance_id required")
  parsed=urlsplit(public_base_url)
  if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
   raise ValueError("public Gateway URL must be a valid HTTPS URL without userinfo/query/fragment")
  self.instance_id=instance_id;self.base=public_base_url.rstrip("/");self.directory=directory
  self.app_factory=app_factory;self.token_service=token_service;self._apps={};self._lock=asyncio.Lock()
 @staticmethod
 def _valid_segment(value:str)->bool:
  return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}",value))
 async def mount_run(self,*,tenant_id:str,run_id:str,bridge:Any)->str:
  if not self._valid_segment(tenant_id) or not self._valid_segment(run_id):
   raise ValueError("tenant and Run IDs must be URL-safe path segments")
  key=(tenant_id,run_id)
  async with self._lock:
   if key in self._apps:raise ValueError("Run already mounted")
   url=f"{self.base}/tenants/{tenant_id}/runs/{run_id}/mcp"
   app=self.app_factory(bridge)
   await self.directory.register(EndpointRecord(tenant_id,run_id,self.instance_id,url,"active",datetime.now(timezone.utc)))
   self._apps[key]=app
  return url
 async def unmount_run(self,*,tenant_id:str,run_id:str)->bool:
  key=(tenant_id,run_id)
  async with self._lock:
   app=self._apps.get(key)
   if app is None:return False
   verifier=getattr(getattr(app,"state",None),"gateway_token_verifier",None)
   if verifier is not None and hasattr(verifier,"revoke"):verifier.revoke()
   token_service=self.token_service or getattr(verifier,"token_service",None)
   if token_service is not None:
    await token_service.revoke_run(tenant_id=tenant_id,run_id=run_id)
   await self.directory.deactivate(tenant_id,run_id)
   self._apps.pop(key,None)
   return True
 async def __call__(self,scope:dict[str,Any],receive:Any,send:Any)->None:
  if scope["type"]!="http":await self._respond(send,404,b"unsupported ASGI scope");return
  p=[x for x in scope.get("path","").split("/") if x]
  if len(p)!=5 or p[0]!="tenants" or p[2]!="runs" or p[4]!="mcp" or not self._valid_segment(p[1]) or not self._valid_segment(p[3]):
   await self._respond(send,404,b"unknown Gateway endpoint");return
  tenant,run=p[1],p[3]
  async with self._lock:app=self._apps.get((tenant,run))
  if app is not None:await app(scope,receive,send);return
  row=await self.directory.get(tenant,run)
  if not row or row.status!="active":await self._respond(send,404,b"Run endpoint inactive");return
  if row.instance_id==self.instance_id:await self._respond(send,503,b"endpoint ownership inconsistent");return
  await send({"type":"http.response.start","status":307,"headers":[(b"location",row.endpoint_url.encode()),(b"cache-control",b"no-store")]})
  await send({"type":"http.response.body","body":b""})
 @staticmethod
 async def _respond(send:Any,status:int,body:bytes)->None:
  await send({"type":"http.response.start","status":status,"headers":[(b"content-type",b"text/plain"),(b"cache-control",b"no-store")]})
  await send({"type":"http.response.body","body":body})
