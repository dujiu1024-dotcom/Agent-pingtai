import asyncio
import unittest

from agent_platform.control_plane.tokens import InMemoryRunTokenStore, RunTokenService
from agent_platform.runtime.lifecycle import (
 InMemoryRunStateStore,
 RunLifecycleManager,
 RunStatus,
 WorkerLeaseLost,
)


class RunLifecycleTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.store=InMemoryRunStateStore(); await self.store.create("t","r"); self.manager=RunLifecycleManager(self.store)
 async def test_cancel_propagates_to_tool_and_run(self):
  await self.manager.start("t","r"); entered=asyncio.Event()
  async def tool(): entered.set(); await asyncio.Event().wait()
  task=asyncio.create_task(self.manager.tools.run("r","call-1",tool(),tenant_id="t"))
  await entered.wait()
  result=await self.manager.cancel("t","r")
  self.assertEqual(result.status,RunStatus.CANCELLING)
  with self.assertRaises(asyncio.CancelledError): await task
 async def test_tool_cancellation_is_tenant_scoped(self):
  entered_a=asyncio.Event(); entered_b=asyncio.Event()
  async def wait(event):
   event.set(); await asyncio.Event().wait()
  task_a=asyncio.create_task(self.manager.tools.run("same-run","call-a",wait(entered_a),tenant_id="tenant-a"))
  task_b=asyncio.create_task(self.manager.tools.run("same-run","call-b",wait(entered_b),tenant_id="tenant-b"))
  await asyncio.gather(entered_a.wait(),entered_b.wait())
  self.assertEqual(await self.manager.tools.cancel_run("same-run",tenant_id="tenant-a"),1)
  with self.assertRaises(asyncio.CancelledError): await task_a
  self.assertFalse(task_b.done())
  await self.manager.tools.cancel_run("same-run",tenant_id="tenant-b")
  with self.assertRaises(asyncio.CancelledError): await task_b
 async def test_timeout_sets_terminal_status(self):
  await self.manager.start("t","r")
  with self.assertRaises(asyncio.TimeoutError):
   await self.manager.run_with_timeout("t","r",asyncio.sleep(1),timeout=0.01)
  self.assertEqual((await self.store.get("t","r")).status,RunStatus.TIMED_OUT)
 async def test_disconnect_defaults_to_continue(self):
  await self.manager.start("t","r"); await self.manager.on_client_disconnect("t","r")
  self.assertEqual((await self.store.get("t","r")).status,RunStatus.RUNNING)
  await self.manager.on_client_disconnect("t","r",policy="cancel")
  self.assertEqual((await self.store.get("t","r")).status,RunStatus.CANCELLING)
 async def test_checkpoint_recovery_skips_human_pause(self):
  await self.manager.start("t","r"); await self.manager.mark_interrupted("t","r",checkpoint_ref="cp-1",reason="worker lost")
  seen=[]
  result=await self.manager.recover(checkpoint_loader=lambda row: _append(seen,"load"),resume=lambda row:_append(seen,"resume"))
  self.assertEqual(result,["r"]); self.assertEqual(seen,["load","resume"])
 async def test_human_paused_run_is_not_auto_resumed(self):
  await self.manager.start("t","r")
  current=await self.store.get("t","r")
  from agent_platform.runtime.lifecycle import RunSnapshot
  await self.store.transition(RunSnapshot("t","r",RunStatus.PAUSED,current.version),expected_version=current.version)
  called=[]
  self.assertEqual(await self.manager.recover(checkpoint_loader=lambda row:_append(called,"load"),
   resume=lambda row:_append(called,"resume")),[])
  self.assertEqual(called,[])
  self.assertEqual((await self.store.get("t","r")).status,RunStatus.PAUSED)
 async def test_queued_run_recovers_from_initial_input_without_checkpoint(self):
  seen=[]
  result=await self.manager.recover(checkpoint_loader=lambda row:_append(seen,"load"),
   resume=lambda row:_append(seen,row.status))
  self.assertEqual(result,["r"])
  self.assertEqual(seen,[RunStatus.RUNNING])
  self.assertEqual((await self.store.get("t","r")).status,RunStatus.RUNNING)
 async def test_interrupted_run_without_checkpoint_fails_closed(self):
  await self.manager.start("t","r")
  await self.manager.mark_interrupted("t","r",checkpoint_ref=None,reason="worker lost")
  self.assertEqual(await self.manager.recover(checkpoint_loader=lambda row:_append([],row),
   resume=lambda row:_append([],row)),[])
  current=await self.store.get("t","r")
  self.assertEqual(current.status,RunStatus.FAILED)
  self.assertIn("no checkpoint",current.last_error)
 async def test_pre_checkpoint_worker_loss_can_restart_from_initial_input(self):
  await self.manager.start("t","r")
  old=await self.store.get("t","r")
  await self.store.transition(__import__("agent_platform.runtime.lifecycle",fromlist=["RunSnapshot"]).RunSnapshot(
   "t","r",RunStatus.INTERRUPTED,old.version,last_error="previous worker lease expired"),expected_version=old.version)
  seen=[]
  self.assertEqual(await self.manager.recover(checkpoint_loader=lambda row:_append(seen,"load"),
   resume=lambda row:_append(seen,row.status)),["r"])
  self.assertEqual(seen,[RunStatus.RUNNING])
 async def test_lost_worker_lease_does_not_fail_run_owned_by_another_worker(self):
  await self.manager.start("t","r")
  await self.manager.mark_interrupted("t","r",checkpoint_ref="cp-1",reason="worker lost")
  async def lost(row): raise WorkerLeaseLost("lease transferred")
  self.assertEqual(await self.manager.recover(checkpoint_loader=lambda row:_append([],row),resume=lost),[])
  self.assertEqual((await self.store.get("t","r")).status,RunStatus.RUNNING)
 async def test_checkpoint_validation_failure_is_persisted_as_failed(self):
  await self.manager.start("t","r")
  await self.manager.mark_interrupted("t","r",checkpoint_ref="missing",reason="worker lost")
  async def fail(row): raise RuntimeError("checkpoint missing")
  self.assertEqual(await self.manager.recover(checkpoint_loader=fail,resume=lambda row:_append([],row)),[])
  current=await self.store.get("t","r")
  self.assertEqual(current.status,RunStatus.FAILED)
  self.assertIn("checkpoint missing",current.last_error)
 async def test_recovery_exception_is_persisted_as_failed(self):
  await self.manager.start("t","r")
  await self.manager.mark_interrupted("t","r",checkpoint_ref="cp-1",reason="worker lost")
  async def fail(row): raise RuntimeError("checkpoint unavailable")
  self.assertEqual(await self.manager.recover(checkpoint_loader=lambda row:_append([],row),resume=fail),[])
  current=await self.store.get("t","r")
  self.assertEqual(current.status,RunStatus.FAILED)
  self.assertIn("checkpoint unavailable",current.last_error)
 async def test_terminal_run_revokes_all_bound_tokens(self):
  tokens=RunTokenService(InMemoryRunTokenStore())
  issued=await tokens.issue(tenant_id="t",run_id="r",agent_id="agent",actor_id="actor",
   endpoint_url="https://gateway.example/tenants/t/runs/r/mcp")
  manager=RunLifecycleManager(self.store,token_service=tokens)
  await manager.start("t","r")
  await manager.finish("t","r",RunStatus.SUCCEEDED)
  self.assertIsNone(await tokens.resolve(issued.token))
 async def test_illegal_transition_rejected(self):
  with self.assertRaises(ValueError):
   await self.store.transition(__import__("agent_platform.runtime.lifecycle",fromlist=["RunSnapshot"]).RunSnapshot("t","r",RunStatus.SUCCEEDED,1),expected_version=1)
 async def test_repeated_cancel_of_terminal_run_revokes_remaining_tokens(self):
  await self.manager.start("t","r")
  await self.manager.finish("t","r",RunStatus.SUCCEEDED)
  tokens=RunTokenService(InMemoryRunTokenStore())
  issued=await tokens.issue(tenant_id="t",run_id="r",agent_id="agent",actor_id="actor",
   endpoint_url="https://gateway.example/tenants/t/runs/r/mcp")
  manager=RunLifecycleManager(self.store,token_service=tokens)
  result=await manager.cancel("t","r")
  self.assertEqual(result.status,RunStatus.SUCCEEDED)
  self.assertIsNone(await tokens.resolve(issued.token))

async def _append(target,value): target.append(value)
if __name__=="__main__": unittest.main()
