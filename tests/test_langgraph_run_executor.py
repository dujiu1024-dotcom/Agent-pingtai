import asyncio
import unittest
from types import SimpleNamespace
from agent_platform.runtime.execution import LangGraphRunExecutor
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager, RunStatus

class FakeGraph:
 def __init__(self):self.calls=[];self.checkpoints={};self.checkpointer=object()
 async def ainvoke(self,payload,*,config):
  thread=config["configurable"]["thread_id"];self.calls.append((payload,thread))
  if payload=="explode":raise RuntimeError("node failed")
  self.checkpoints[thread]="checkpoint-1"
  return {"result":payload}
 async def aget_state(self,config):
  thread=config["configurable"]["thread_id"]
  checkpoint=self.checkpoints.get(thread)
  return SimpleNamespace(config={"configurable":{"checkpoint_id":checkpoint}})

class LangGraphRunExecutorTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.store=InMemoryRunStateStore();self.manager=RunLifecycleManager(self.store)
  self.graph=FakeGraph();self.executor=LangGraphRunExecutor(graph=self.graph,lifecycle=self.manager,timeout_seconds=1)
 async def test_execute_persists_terminal_state_and_checkpoint(self):
  await self.store.create("tenant","run")
  result=await self.executor.execute(tenant_id="tenant",run_id="run",input={"prompt":"hello"})
  row=await self.store.get("tenant","run")
  self.assertEqual(result,{"result":{"prompt":"hello"}})
  self.assertEqual(row.status,RunStatus.SUCCEEDED);self.assertEqual(row.checkpoint_ref,"checkpoint-1")
  self.assertEqual(self.graph.calls[0][1],"tenant:run")
 async def test_execute_failure_is_persisted(self):
  await self.store.create("tenant","run")
  with self.assertRaisesRegex(RuntimeError,"node failed"):
   await self.executor.execute(tenant_id="tenant",run_id="run",input="explode")
  self.assertEqual((await self.store.get("tenant","run")).status,RunStatus.FAILED)
 async def test_execute_timeout_is_terminal(self):
  class SlowGraph(FakeGraph):
   async def ainvoke(self,payload,*,config):await asyncio.sleep(1)
  await self.store.create("tenant","slow")
  executor=LangGraphRunExecutor(graph=SlowGraph(),lifecycle=self.manager,timeout_seconds=0.01)
  with self.assertRaises(asyncio.TimeoutError):
   await executor.execute(tenant_id="tenant",run_id="slow",input={})
  self.assertEqual((await self.store.get("tenant","slow")).status,RunStatus.TIMED_OUT)
 async def test_recovery_resumes_checkpoint_and_queued_input(self):
  await self.store.create("tenant","interrupted");await self.manager.start("tenant","interrupted")
  await self.manager.mark_interrupted("tenant","interrupted",checkpoint_ref="checkpoint-1",reason="worker lost")
  self.graph.checkpoints["tenant:interrupted"]="checkpoint-1"
  await self.store.create("tenant","queued")
  recovered=await self.executor.recover(load_initial_input=lambda row:{"initial":row.run_id})
  self.assertEqual(set(recovered),{"interrupted","queued"})
  self.assertEqual((await self.store.get("tenant","interrupted")).status,RunStatus.SUCCEEDED)
  self.assertEqual((await self.store.get("tenant","queued")).status,RunStatus.SUCCEEDED)
  self.assertEqual(self.graph.calls[-1][0],{"initial":"queued"})
 async def test_failed_worker_lease_does_not_start_run(self):
  class LeaseRejectingStore(InMemoryRunStateStore):
   async def acquire_lease(self,tenant_id,run_id,worker_id,*,lease_seconds):
    return False
  store=LeaseRejectingStore();manager=RunLifecycleManager(store);graph=FakeGraph()
  executor=LangGraphRunExecutor(graph=graph,lifecycle=manager,timeout_seconds=1)
  await store.create("tenant","unclaimed")
  from agent_platform.runtime.lifecycle import WorkerLeaseLost
  with self.assertRaises(WorkerLeaseLost):
   await executor.execute(tenant_id="tenant",run_id="unclaimed",input={"prompt":"must not run"})
  self.assertEqual((await store.get("tenant","unclaimed")).status,RunStatus.QUEUED)
  self.assertEqual(graph.calls,[])

 async def test_cancellation_stops_graph_task_and_marks_run_interrupted(self):
  started=asyncio.Event();cancelled=asyncio.Event()
  class BlockingGraph(FakeGraph):
   async def ainvoke(self,payload,*,config):
    started.set()
    try: await asyncio.Event().wait()
    except asyncio.CancelledError:
     cancelled.set()
     raise
  await self.store.create("tenant","cancelled")
  executor=LangGraphRunExecutor(graph=BlockingGraph(),lifecycle=self.manager,timeout_seconds=10)
  task=asyncio.create_task(executor.execute(tenant_id="tenant",run_id="cancelled",input={}))
  await started.wait()
  task.cancel()
  with self.assertRaises(asyncio.CancelledError): await task
  self.assertTrue(cancelled.is_set())
  self.assertEqual((await self.store.get("tenant","cancelled")).status,RunStatus.INTERRUPTED)

 async def test_lost_worker_lease_marks_run_interrupted(self):
  started=asyncio.Event();cancelled=asyncio.Event()
  class LeaseExpiringStore(InMemoryRunStateStore):
   async def acquire_lease(self,tenant_id,run_id,worker_id,*,lease_seconds):
    return True
   async def heartbeat(self,tenant_id,run_id,worker_id,*,lease_seconds):
    return False
   async def release_lease(self,tenant_id,run_id,worker_id):
    return True
  class BlockingGraph(FakeGraph):
   async def ainvoke(self,payload,*,config):
    started.set()
    try: await asyncio.Event().wait()
    except asyncio.CancelledError:
     cancelled.set()
     raise
  store=LeaseExpiringStore();manager=RunLifecycleManager(store);graph=BlockingGraph()
  executor=LangGraphRunExecutor(graph=graph,lifecycle=manager,timeout_seconds=10,lease_seconds=5)
  await store.create("tenant","lease-lost")
  from agent_platform.runtime.lifecycle import WorkerLeaseLost
  with self.assertRaises(WorkerLeaseLost):
   await executor.execute(tenant_id="tenant",run_id="lease-lost",input={})
  self.assertTrue(started.is_set())
  self.assertTrue(cancelled.is_set())
  self.assertEqual((await store.get("tenant","lease-lost")).status,RunStatus.INTERRUPTED)

if __name__=="__main__":unittest.main()
