"""Real PostgreSQL integration for Gateway credentials, endpoint ownership and Run state."""
import os\nimport unittest
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
DSN=os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
 from psycopg import AsyncConnection
 from psycopg.rows import dict_row
 from agent_platform.control_plane.tokens import PostgresRunTokenStore,RunTokenService
 from agent_platform.gateway.postgres import PostgresEndpointDirectory
 from agent_platform.gateway.service import EndpointRecord
 from agent_platform.runtime.lifecycle import RunSnapshot,RunStatus
 from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore
@unittest.skipUnless(DSN,"AGENT_PLATFORM_TEST_DSN is not configured")
class PostgresGatewayRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tenant=f"gateway-it-{uuid4().hex}"; self.agent=f"agent-{uuid4().hex}"
  self.template=f"template-{uuid4().hex}"; self.run=str(uuid4())
  async with await AsyncConnection.connect(DSN,autocommit=True) as conn:
   for path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
    await conn.execute(path.read_text(),prepare=False)
  async with await AsyncConnection.connect(DSN) as conn:
   await conn.execute("INSERT INTO platform_tenants(tenant_id,display_name) VALUES(%s,%s)",(self.tenant,"Gateway integration"))
   await conn.execute("INSERT INTO platform_agents(tenant_id,agent_id,display_name,role_definition) VALUES(%s,%s,'Integration Agent','test')",(self.tenant,self.agent))
   await conn.execute("INSERT INTO platform_model_templates(tenant_id,template_id,version,definition,status,created_by) VALUES(%s,%s,1,'{}'::jsonb,'active','integration')",(self.tenant,self.template))
   await conn.execute("INSERT INTO platform_runs(tenant_id,run_id,agent_id,template_id,template_version,status) VALUES(%s,%s,%s,%s,1,'queued')",(self.tenant,self.run,self.agent,self.template))
   await conn.commit()
  async def connect(): return await AsyncConnection.connect(DSN,row_factory=dict_row)
  self.connect=connect; self.tokens=RunTokenService(PostgresRunTokenStore(connect))
  self.directory=PostgresEndpointDirectory(connect); self.states=PostgresRunStateStore(connect)
 async def test_token_revocation_is_shared_and_cleanup_is_persistent(self):
  issued=await self.tokens.issue(tenant_id=self.tenant,run_id=self.run,agent_id=self.agent,actor_id="integration-user",endpoint_url=f"https://gateway.example/tenants/{self.tenant}/runs/{self.run}/mcp")
  other=RunTokenService(PostgresRunTokenStore(self.connect))
  self.assertIsNotNone(await other.resolve(issued.token))
  self.assertEqual(await other.revoke_run(tenant_id=self.tenant,run_id=self.run),1)
  self.assertIsNone(await self.tokens.resolve(issued.token))
  another=await self.tokens.issue(tenant_id=self.tenant,run_id=self.run,agent_id=self.agent,actor_id="integration-user",endpoint_url="https://gateway.example/mcp")
  async with await self.connect() as conn:
   await conn.execute("UPDATE platform_run_tokens SET expires_at=now()-interval '1 second' WHERE token_id=%s",(another.token_id,))
   await conn.commit()
  self.assertEqual(await self.tokens.cleanup_expired(),1)
  self.assertIsNone(await self.tokens.resolve(another.token))
 async def test_endpoint_directory_is_shared_and_deactivates(self):
  record=EndpointRecord(self.tenant,self.run,"gateway-a",f"https://gateway.example/tenants/{self.tenant}/runs/{self.run}/mcp","active",datetime.now(timezone.utc))
  await self.directory.register(record)
  other=PostgresEndpointDirectory(self.connect); found=await other.get(self.tenant,self.run)
  self.assertEqual(found.instance_id,"gateway-a")
  self.assertTrue(await other.deactivate(self.tenant,self.run))
  self.assertEqual((await self.directory.get(self.tenant,self.run)).status,"inactive")
 async def test_run_state_store_enforces_transitions_and_optimistic_version(self):
  queued=await self.states.get(self.tenant,self.run); self.assertEqual(queued.status,RunStatus.QUEUED)
  running=await self.states.transition(RunSnapshot(self.tenant,self.run,RunStatus.RUNNING,queued.version),expected_version=queued.version)
  self.assertEqual(running.version,queued.version+1)
  with self.assertRaises(ValueError):
   await self.states.transition(RunSnapshot(self.tenant,self.run,RunStatus.QUEUED,running.version),expected_version=running.version)
  with self.assertRaises(RuntimeError):
   await self.states.transition(RunSnapshot(self.tenant,self.run,RunStatus.SUCCEEDED,queued.version),expected_version=queued.version)
  done=await self.states.transition(RunSnapshot(self.tenant,self.run,RunStatus.SUCCEEDED,running.version),expected_version=running.version)
  self.assertEqual(done.status,RunStatus.SUCCEEDED)
if __name__=="__main__":unittest.main()
