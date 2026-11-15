import unittest
from types import SimpleNamespace

from agent_platform.control_plane.tokens import InMemoryRunTokenStore, RunTokenService
from agent_platform.gateway.service import GatewayService, InMemoryEndpointDirectory
class GatewayServiceTests(unittest.IsolatedAsyncioTestCase):
 async def test_isolates_runs_and_routes_to_owner_instance(self):
  directory=InMemoryEndpointDirectory(); app1=object(); app2=object()
  one=GatewayService(instance_id="gw-1",public_base_url="https://gw1.example",directory=directory,app_factory=lambda bridge:app1)
  two=GatewayService(instance_id="gw-2",public_base_url="https://gw2.example",directory=directory,app_factory=lambda bridge:app2)
  await one.mount_run(tenant_id="t",run_id="r1",bridge=object())
  await two.mount_run(tenant_id="t",run_id="r2",bridge=object())
  self.assertIs(one._apps[("t","r1")],app1); self.assertIs(two._apps[("t","r2")],app2)
  sent=[]
  async def send(event): sent.append(event)
  await one({"type":"http","path":"/tenants/t/runs/r2/mcp"},None,send)
  self.assertEqual(sent[0]["status"],307); self.assertIn(b"gw2.example",dict(sent[0]["headers"])[b"location"])
  await one.unmount_run(tenant_id="t",run_id="r1"); await two.unmount_run(tenant_id="t",run_id="r2")
 async def test_unmount_revokes_persistent_run_tokens(self):
  directory=InMemoryEndpointDirectory()
  tokens=RunTokenService(InMemoryRunTokenStore())
  issued=await tokens.issue(tenant_id="t",run_id="r",agent_id="a",actor_id="u",endpoint_url="https://gw.example/tenants/t/runs/r/mcp")
  service=GatewayService(instance_id="gw",public_base_url="https://gw.example",directory=directory,
   app_factory=lambda _:SimpleNamespace(state=SimpleNamespace()),token_service=tokens)
  await service.mount_run(tenant_id="t",run_id="r",bridge=object())
  self.assertIsNotNone(await tokens.resolve(issued.token))
  self.assertTrue(await service.unmount_run(tenant_id="t",run_id="r"))
  self.assertIsNone(await tokens.resolve(issued.token))
 async def test_expired_owner_lease_can_be_taken_over_without_old_owner_deactivating_new_owner(self):
  from datetime import datetime, timedelta, timezone
  directory=InMemoryEndpointDirectory()
  one=GatewayService(instance_id="gw-1",public_base_url="https://gw1.example",directory=directory,app_factory=lambda _:object(),lease_seconds=60)
  two=GatewayService(instance_id="gw-2",public_base_url="https://gw2.example",directory=directory,app_factory=lambda _:object(),lease_seconds=60)
  await one.mount_run(tenant_id="t",run_id="r",bridge=object())
  async with directory._lock:
   old=directory._rows[("t","r")]
   directory._rows[("t","r")]=type(old)(old.tenant_id,old.run_id,old.instance_id,old.endpoint_url,old.status,old.created_at,datetime.now(timezone.utc)-timedelta(seconds=1))
  await two.mount_run(tenant_id="t",run_id="r",bridge=object())
  self.assertEqual((await directory.get("t","r")).instance_id,"gw-2")
  self.assertFalse(await directory.deactivate("t","r","gw-1"))
  self.assertEqual((await directory.get("t","r")).instance_id,"gw-2")
  await one.unmount_run(tenant_id="t",run_id="r")
  await two.unmount_run(tenant_id="t",run_id="r")
 async def test_asgi_shutdown_unmounts_runs_and_revokes_tokens(self):
  directory=InMemoryEndpointDirectory(); tokens=RunTokenService(InMemoryRunTokenStore())
  issued=await tokens.issue(tenant_id="t",run_id="r",agent_id="a",actor_id="u",endpoint_url="https://gw.example/tenants/t/runs/r/mcp")
  service=GatewayService(instance_id="gw",public_base_url="https://gw.example",directory=directory,app_factory=lambda _:SimpleNamespace(state=SimpleNamespace()),token_service=tokens)
  await service.mount_run(tenant_id="t",run_id="r",bridge=object())
  messages=iter([{"type":"lifespan.startup"},{"type":"lifespan.shutdown"}]);sent=[]
  async def receive():return next(messages)
  async def send(event):sent.append(event)
  await service({"type":"lifespan"},receive,send)
  self.assertEqual([item["type"] for item in sent],["lifespan.startup.complete","lifespan.shutdown.complete"])
  self.assertEqual((await directory.get("t","r")).status,"inactive")
  self.assertIsNone(await tokens.resolve(issued.token))
 async def test_local_gateway_fails_closed_after_lease_deadline(self):
  from datetime import datetime, timezone, timedelta
  directory=InMemoryEndpointDirectory()
  service=GatewayService(instance_id="gw",public_base_url="https://gw.example",directory=directory,app_factory=lambda _:object(),lease_seconds=60)
  await service.mount_run(tenant_id="t",run_id="r",bridge=object())
  service._lease_expires[("t","r")]=datetime.now(timezone.utc)-timedelta(seconds=1)
  sent=[]
  async def send(event):sent.append(event)
  await service({"type":"http","path":"/tenants/t/runs/r/mcp"},None,send)
  self.assertEqual(sent[0]["status"],503)
  await service.unmount_run(tenant_id="t",run_id="r")
 async def test_gateway_renews_endpoint_owner_lease(self):
  directory=InMemoryEndpointDirectory()
  service=GatewayService(instance_id="gw",public_base_url="https://gw.example",directory=directory,app_factory=lambda _:object(),lease_seconds=6)
  await service.mount_run(tenant_id="t",run_id="r",bridge=object())
  before=(await directory.get("t","r")).lease_expires_at
  self.assertTrue(await directory.heartbeat("t","r","gw",lease_seconds=12))
  after=(await directory.get("t","r")).lease_expires_at
  self.assertGreater(after,before)
  await service.unmount_run(tenant_id="t",run_id="r")
 async def test_rejects_unsafe_run_path_segments(self):
  service=GatewayService(instance_id="gw",public_base_url="https://gw.example",directory=InMemoryEndpointDirectory(),app_factory=lambda _:object())
  with self.assertRaises(ValueError):
   await service.mount_run(tenant_id="../other",run_id="r",bridge=object())
 async def test_unmount_revokes_run_verifier(self):
  directory=InMemoryEndpointDirectory(); verifier=SimpleNamespace(revoked=False)
  verifier.revoke=lambda:setattr(verifier,"revoked",True)
  app=SimpleNamespace(state=SimpleNamespace(gateway_token_verifier=verifier))
  service=GatewayService(instance_id="gw",public_base_url="https://gw.example",directory=directory,app_factory=lambda _:app)
  await service.mount_run(tenant_id="t",run_id="r",bridge=object())
  self.assertTrue(await service.unmount_run(tenant_id="t",run_id="r")); self.assertTrue(verifier.revoked)
 async def test_heartbeat_retries_after_transient_directory_failure(self):
  import asyncio
  class FlakyDirectory(InMemoryEndpointDirectory):
   def __init__(self):
    super().__init__(); self.calls=0; self.recovered=asyncio.Event()
   async def heartbeat(self,tenant_id,run_id,instance_id,*,lease_seconds):
    self.calls+=1
    if self.calls==1: raise RuntimeError("temporary database outage")
    self.recovered.set()
    return await super().heartbeat(tenant_id,run_id,instance_id,lease_seconds=lease_seconds)
  directory=FlakyDirectory()
  service=GatewayService(instance_id="gw",public_base_url="https://gw.example",directory=directory,
   app_factory=lambda _:object(),lease_seconds=6)
  await service.mount_run(tenant_id="t",run_id="r",bridge=object())
  await asyncio.wait_for(directory.recovered.wait(),timeout=6)
  self.assertGreaterEqual(directory.calls,2)
  self.assertFalse(service._heartbeat_tasks[("t","r")].done())
  await service.unmount_run(tenant_id="t",run_id="r")

if __name__=="__main__": unittest.main()
