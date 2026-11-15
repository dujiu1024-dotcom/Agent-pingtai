import unittest\nfrom agent_platform.control_plane.tokens import InMemoryRunTokenStore,RunTokenService
from types import SimpleNamespace
from agent_platform.gateway.service import GatewayService,InMemoryEndpointDirectory
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
if __name__=="__main__": unittest.main()
