import asyncio
import unittest
from types import SimpleNamespace

from agent_platform.runtime.execution import LangGraphRunExecutor
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager, RunStatus


class FakeGraph:
 def __init__(self):self.calls=[];self.configs=[];self.checkpoints={};self.checkpointer=object()
 async def ainvoke(self,payload,*,config):
  thread=config["configurable"]["thread_id"];self.calls.append((payload,thread));self.configs.append(config)
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
 async def test_pinned_registry_snapshot_is_passed_to_graph_config(self):
  class SnapshotService:
   async def get(self,*,tenant_id,run_id):
    return SimpleNamespace(
     entries=({"kind":"agent","item_id":"researcher","version":3},),
     digest="d"*64,
    )
  await self.store.create("tenant","pinned")
  executor=LangGraphRunExecutor(
   graph=self.graph,lifecycle=self.manager,timeout_seconds=1,
   registry_snapshot_service=SnapshotService(),
  )
  await executor.execute(tenant_id="tenant",run_id="pinned",input={"task":"go"})
  configurable=self.graph.configs[0]["configurable"]
  self.assertEqual(configurable["platform_registry_snapshot"][0]["version"],3)
  self.assertEqual(configurable["platform_registry_snapshot_digest"],"d"*64)
  self.assertEqual(configurable["platform_tenant_id"],"tenant")
  self.assertEqual(configurable["platform_run_id"],"pinned")

 async def test_missing_pinned_snapshot_does_not_start_run(self):
  class MissingSnapshotService:
   async def get(self,*,tenant_id,run_id):
    raise LookupError("snapshot missing")
  await self.store.create("tenant","missing-snapshot")
  executor=LangGraphRunExecutor(
   graph=self.graph,lifecycle=self.manager,timeout_seconds=1,
   registry_snapshot_service=MissingSnapshotService(),
  )
  with self.assertRaisesRegex(LookupError,"snapshot missing"):
   await executor.execute(tenant_id="tenant",run_id="missing-snapshot",input={})
  self.assertEqual((await self.store.get("tenant","missing-snapshot")).status,RunStatus.QUEUED)
  self.assertEqual(self.graph.calls,[])

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
 async def test_recovery_applies_human_interventions_before_graph_resume(self):
  await self.store.create("tenant","human-resume")
  await self.manager.start("tenant","human-resume")
  await self.manager.mark_interrupted(
   "tenant","human-resume",checkpoint_ref="checkpoint-1",reason="human resume requested",
  )
  self.graph.checkpoints["tenant:human-resume"]="checkpoint-1"
  class InterventionStore:
   async def apply_pending(self,*,tenant_id,run_id,worker_id,applier):
    await applier({"kind":"context","payload":{"guidance":"use verified evidence"}})
    return 1
  async def apply(graph,config,intervention):
   graph.applied_intervention=intervention
  executor=LangGraphRunExecutor(
   graph=self.graph,lifecycle=self.manager,timeout_seconds=1,
   intervention_store=InterventionStore(),intervention_applier=apply,
  )
  recovered=await executor.recover(load_initial_input=lambda row:{})
  self.assertEqual(recovered,["human-resume"])
  self.assertEqual(self.graph.applied_intervention["payload"]["guidance"],"use verified evidence")

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
  # 先持久化 CANCELLING（显式取消路径），再取消 executor task
  await self.manager.cancel("tenant","cancelled")
  task.cancel()
  with self.assertRaises(asyncio.CancelledError): await task
  self.assertTrue(cancelled.is_set())
  self.assertEqual((await self.store.get("tenant","cancelled")).status,RunStatus.CANCELLED)

 async def test_executor_pause_preserves_checkpoint_and_paused_status(self):
  started=asyncio.Event()
  class PausableGraph(FakeGraph):
   async def ainvoke(self,payload,*,config):
    self.checkpoints[config["configurable"]["thread_id"]]="checkpoint-pause"
    started.set()
    await asyncio.Event().wait()
  graph=PausableGraph()
  await self.store.create("tenant","pause")
  executor=LangGraphRunExecutor(graph=graph,lifecycle=self.manager,timeout_seconds=10)
  task=asyncio.create_task(executor.execute(tenant_id="tenant",run_id="pause",input={"task":"wait"}))
  await started.wait()
  paused=await executor.pause(tenant_id="tenant",run_id="pause")
  self.assertEqual(paused.status,RunStatus.PAUSED)
  self.assertEqual(paused.checkpoint_ref,"checkpoint-pause")
  with self.assertRaises(asyncio.CancelledError):
   await task
  self.assertEqual((await self.store.get("tenant","pause")).status,RunStatus.PAUSED)

 async def test_lost_worker_lease_leaves_recovery_to_new_owner(self):
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
  self.assertEqual((await store.get("tenant","lease-lost")).status,RunStatus.RUNNING)


 async def test_claimed_stale_run_restarts_from_persisted_initial_input(self):
  await self.store.create("tenant","stale")
  await self.manager.start("tenant","stale")
  snapshot=await self.store.get("tenant","stale")
  result=await self.executor.resume_claimed(
   snapshot=snapshot,input={"prompt":"resume after worker loss"},
  )
  self.assertEqual(result,{"result":{"prompt":"resume after worker loss"}})
  self.assertEqual((await self.store.get("tenant","stale")).status,RunStatus.SUCCEEDED)

 async def test_resume_refuses_a_lease_already_transferred_to_another_worker(self):
  class LeaseTransferredStore(InMemoryRunStateStore):
   async def owns_lease(self,tenant_id,run_id,worker_id):
    return False
  store=LeaseTransferredStore();manager=RunLifecycleManager(store);graph=FakeGraph()
  await store.create("tenant","lease-transferred")
  await manager.start("tenant","lease-transferred")
  snapshot=await store.get("tenant","lease-transferred")
  executor=LangGraphRunExecutor(
   graph=graph,lifecycle=manager,worker_id="old-worker",timeout_seconds=1,
  )
  from agent_platform.runtime.lifecycle import WorkerLeaseLost
  with self.assertRaises(WorkerLeaseLost):
   await executor.resume_claimed(snapshot=snapshot,input={"prompt":"do not duplicate"})
  self.assertEqual((await store.get("tenant","lease-transferred")).status,RunStatus.RUNNING)
  self.assertEqual(graph.calls,[])

 async def test_claimed_cancellation_is_confirmed_without_restarting_graph(self):
  await self.store.create("tenant","remote-cancel")
  await self.manager.start("tenant","remote-cancel")
  current=await self.store.get("tenant","remote-cancel")
  from agent_platform.runtime.lifecycle import RunSnapshot
  await self.store.transition(
   RunSnapshot("tenant","remote-cancel",RunStatus.CANCELLING,current.version),
   expected_version=current.version,
  )
  claimed=await self.store.get("tenant","remote-cancel")
  result=await self.executor.resume_claimed(
   snapshot=claimed,input={"prompt":"must not execute"},
  )
  self.assertIsNone(result)
  self.assertEqual((await self.store.get("tenant","remote-cancel")).status,RunStatus.CANCELLED)
  self.assertEqual(self.graph.calls,[])

 async def test_recovery_lease_loss_leaves_run_for_new_lease_owner(self):
  started=asyncio.Event();cancelled=asyncio.Event()
  class LeaseExpiringStore(InMemoryRunStateStore):
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
  await store.create("tenant","recovery-lease-lost")
  await manager.start("tenant","recovery-lease-lost")
  await manager.mark_interrupted("tenant","recovery-lease-lost",checkpoint_ref="checkpoint-1",reason="previous worker lost")
  graph.checkpoints["tenant:recovery-lease-lost"]="checkpoint-1"
  recovered=await executor.recover(load_initial_input=lambda row:{})
  self.assertEqual(recovered,[])
  self.assertTrue(started.is_set())
  self.assertTrue(cancelled.is_set())
  self.assertEqual((await store.get("tenant","recovery-lease-lost")).status,RunStatus.RUNNING)


 async def test_registry_runtime_is_prepared_before_execution_and_released_afterward(self):
  class RuntimeProvider:
   def __init__(self):self.events=[];self.arguments=None
   async def prepare(self,**kwargs):
    self.events.append("prepare");self.arguments=kwargs
   async def release(self,tenant_id,run_id):
    self.events.append("release")
  provider=RuntimeProvider()
  await self.store.create("tenant","runtime")
  executor=LangGraphRunExecutor(
   graph=self.graph,lifecycle=self.manager,timeout_seconds=1,
   registry_runtime_provider=provider,model_template_id="general",
   required_tools=("search",),
  )
  await executor.execute(tenant_id="tenant",run_id="runtime",input={"task":"go"})
  self.assertEqual(provider.events,["prepare","release"])
  self.assertEqual(provider.arguments["model_template_id"],"general")
  self.assertEqual(provider.arguments["required_tools"],("search",))

 async def test_recovery_rebuilds_registry_runtime_from_pinned_snapshot(self):
  class RuntimeProvider:
   def __init__(self):self.prepared=[];self.released=[]
   async def prepare(self,**kwargs):self.prepared.append(kwargs)
   async def release(self,tenant_id,run_id):self.released.append((tenant_id,run_id))
  provider=RuntimeProvider()
  await self.store.create("tenant","recover-runtime")
  await self.manager.start("tenant","recover-runtime")
  await self.manager.mark_interrupted(
   "tenant","recover-runtime",checkpoint_ref="checkpoint-1",reason="worker lost",
  )
  self.graph.checkpoints["tenant:recover-runtime"]="checkpoint-1"
  executor=LangGraphRunExecutor(
   graph=self.graph,lifecycle=self.manager,timeout_seconds=1,
   registry_runtime_provider=provider,model_template_id="general",
  )
  recovered=await executor.recover(load_initial_input=lambda row:{"task":"initial"})
  self.assertEqual(recovered,["recover-runtime"])
  self.assertEqual(len(provider.prepared),1)
  self.assertEqual(provider.released,[("tenant","recover-runtime")])



