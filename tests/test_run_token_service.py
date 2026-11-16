import asyncio
import unittest
from datetime import timedelta

from agent_platform.control_plane.tokens import InMemoryRunTokenStore, RunTokenService


class RunTokenServiceTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.store=InMemoryRunTokenStore(); self.service=RunTokenService(self.store)
 async def test_issue_resolve_revoke_and_cleanup(self):
  issued=await self.service.issue(tenant_id="t1",run_id="r1",agent_id="a1",actor_id="u1",endpoint_url="https://gw.example/runs/r1/mcp")
  self.assertNotEqual(issued.token,issued.token_id)
  record=await self.service.resolve(issued.token,endpoint_url=issued.endpoint_url)
  self.assertEqual(record.run_id,"r1")
  self.assertIsNone(await self.service.resolve(issued.token,endpoint_url="https://other.example/mcp"))
  self.assertTrue(await self.service.revoke(tenant_id="t1",token_id=issued.token_id))
  self.assertIsNone(await self.service.resolve(issued.token))
  expired=await self.service.issue(tenant_id="t1",run_id="r2",agent_id="a1",actor_id="u1",endpoint_url="https://gw.example/runs/r2/mcp",ttl=timedelta(seconds=1))
  row=await self.store.get_by_digest(__import__("hashlib").sha256(expired.token.encode()).hexdigest())
  await self.store.delete_expired(row.expires_at+timedelta(seconds=1))
  self.assertIsNone(await self.store.get_by_digest(row.token_digest))
 async def test_run_revocation_and_ttl_guard(self):
  a=await self.service.issue(tenant_id="t1",run_id="r1",agent_id="a",actor_id="u",endpoint_url="https://gw.example/r1")
  b=await self.service.issue(tenant_id="t1",run_id="r1",agent_id="a",actor_id="u",endpoint_url="https://gw.example/r1")
  self.assertEqual(await self.service.revoke_run(tenant_id="t1",run_id="r1"),2)
  self.assertIsNone(await self.service.resolve(a.token)); self.assertIsNone(await self.service.resolve(b.token))
  with self.assertRaises(ValueError):
   await self.service.issue(tenant_id="t",run_id="r",agent_id="a",actor_id="u",endpoint_url="http://gateway.example/mcp")
 async def test_parallel_issuance_has_unique_tokens(self):
  items=await asyncio.gather(*[self.service.issue(tenant_id="t",run_id=f"r{i}",agent_id="a",actor_id="u",endpoint_url=f"https://gw.example/r{i}") for i in range(20)])
  self.assertEqual(len({x.token for x in items}),20)
if __name__=="__main__": unittest.main()
