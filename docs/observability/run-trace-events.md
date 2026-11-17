# Run Trace 事件采集（C-29-01）

## 事件契约

RunTraceEvent 是统一的追加式事件记录，包含 tenant_id、run_id、单 Run 单调递增 sequence、UUID event_id、event_type、UTC occurred_at、可选 step_id / parent_event_id 和经过脱敏的 JSON payload。

标准事件：
- RUN_START：Run 生命周期进入 running 后写入，携带 worker、checkpoint、session/registry 开关等 Run 元信息。
- STEP_START / STEP_FINISH：LangGraph/LangChain chain callback。
- LLM_REQUEST / LLM_RESPONSE：模型请求/响应 callback，只记录模型名、批次数量、generation 数等元信息，不存 prompt/response 正文。
- TOOL_CALL：工具调用 callback，只记录工具名和输入长度，不存工具输入/输出正文。
- ERROR：Run、step、LLM、tool 错误，记录异常类型，不复制异常正文。
- COMPLETE：Run 成功终态事件。
- BLACKBOARD_EVENT：引用 #13 Blackboard 的 event_id/type/entry/version/correlation_id；Blackboard 本身仍是状态和原始事件的权威存储。

## 持久化与查询

PostgreSQL adapter：PostgresRunTraceRepository，表 platform_run_trace_events。主键为 (tenant_id, run_id, sequence)，并对 (tenant_id, run_id, event_id) 唯一约束。追加通过 tenant+Run advisory transaction lock 分配 sequence，按 (tenant_id, run_id, sequence) 分页读取。表只追加，不提供 update/delete API。

部署启动时对 repository 调用 await repository.ensure_schema()；DDL 为 CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS，可重复执行。该步骤应由平台 DB bootstrap/migration 阶段执行，不应在每个请求中执行。InMemoryRunTraceRepository 用于测试和本地开发。

## 执行接线

LangGraphRunExecutor(trace_service=...)：
1. Run 状态转为 running 后写 RUN_START；
2. 将 RunTraceCallbackHandler 注入 LangGraph RunnableConfig callbacks，采集 chain/LLM/tool 事件；
3. 执行成功并持久化 Session assistant 消息后写 COMPLETE；超时、取消、lease loss 或执行异常写 ERROR。

通过 LangfuseRunTraceExporter(LangfuseObservationSink(...)) 可把同一事件投影到项目已有 Langfuse v3 SDK；PostgreSQL 是事件事实源，Langfuse 投影失败不会影响已经落库的事件。

## Blackboard #13 复用

Blackboard 的 RUN-scope 事件通过 RunTraceService.emit_blackboard_event(...) 引用进同一 Run 时间线。桥接只保存 Blackboard event ID 和版本等引用元信息，不复制 Blackboard payload，不改变 #13 的事件发布/存储契约。调用方应在读取到 scope=run 且 scope_id=run_id 的 Blackboard 事件时调用该方法。

## 安全与限制

- Trace payload 统一经过 redact_payload；禁止存 prompt、模型正文、工具参数/结果或 secret。
- 所有读取均要求 tenant_id + run_id；不同租户的同名 Run 不共享事件。
- Langfuse 是可选的观察投影；本地测试不需要 Langfuse SDK 或真实服务。
- C-29-01 只交付事件采集与持久化，不实现 Replay API 或 UI。
