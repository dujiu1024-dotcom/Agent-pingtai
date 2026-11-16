"""MCP SDK Bearer verifier backed by the shared RunTokenService."""
from __future__ import annotations

from typing import Any


class PersistentRunTokenVerifier:
 """No process-local credential cache: revocation is visible across instances."""
 def __init__(self,*,token_service:Any,resource_server_url:str)->None:
  self.token_service=token_service;self.resource_server_url=resource_server_url
 async def verify_token(self,token:str)->Any|None:
  record=await self.token_service.resolve(token,endpoint_url=self.resource_server_url)
  if record is None:return None
  try:from mcp.server.auth.provider import AccessToken  # noqa: I001
  except ImportError as exc:raise RuntimeError("MCP SDK auth support required") from exc
  return AccessToken(token=token,client_id=record.actor_id,scopes=list(record.scopes),
    resource=record.endpoint_url,subject=record.actor_id)
