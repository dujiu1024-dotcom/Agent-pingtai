import asyncio,unittest
from agent_platform.runtime.lifecycle import InMemoryRunStateStore,RunLifecycleManager,RunStatus,ToolCallRegistry
class RunLifecycleTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.store=InMemoryRunStateStore(); await self.store.create("t","r"); self.manager=RunLifecycleManager(self.store)
 async def test_cancel_propagates_to_tool_and_run(self):
  await self.manager.start("t","r"); entered=asyncio.Event()
  async def tool(): entered.set(); await asyncio.Event().wait()
  task=asyncio.create_task(self.manager.tools.run("r","call-1",tool()))
  await entered.wait()
  result=await self.manager.cancel("t","r")
  self.assertEqual(result.status,RunStatus.CANCELLED)
  with self.assertRaises(asyncio.CancelledError): await task
 async def test_timeout_sets_terminal_status(self):
  await self.manager.start("t","r")
  with self.assertRaises(asyncio.TimeoutError):
   await self.manager.run_with_timeout("t","r",asyncio.sleep(1),timeout=0.01)
  self.assertEqual((await self.store.get("t","r")).status,RunStatus.TIMED_OUT)
 async def test_disconnect_defaults_to_continue(self):
  await self.manager.start("t","r"); await self.manager.on_client_disconnect("t","r")
  self.assertEqual((await self.store.get("t","r")).status,RunStatus.RUNNING)
  await self.manager.on_client_disconnect("t","r",policy="cancel")
  self.assertEqual((await self.store.get("t","r")).status,RunStatus.CANCELLED)
 async def test_checkpoint_recovery_skips_human_pause(self):
  await self.manager.start("t","r"); await self.manager.mark_interrupted("t","r",checkpoint_ref="cp-1",reason="worker lost")
  seen=[]
  result=await self.manager.recover(checkpoint_loader=lambda row: _append(seen,"load"),resume=lambda row:_append(seen,"resume"))
  self.assertEqual(result,["r"]); self.assertEqual(seen,["load","resume"])
 async def test_illegal_transition_rejected(self):
  with self.assertRaises(ValueError):
   await self.store.transition(__import__("agent_platform.runtime.lifecycle",fromlist=["RunSnapshot"]).RunSnapshot("t","r",RunStatus.SUCCEEDED,1),expected_version=1)
async def _append(target,value): target.append(value)
if __name__=="__main__": unittest.main()
