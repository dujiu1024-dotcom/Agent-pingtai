# 可观测、评测与 Prompt 版本治理实现说明

- 关联：Issue #16 / 总控 Issue #4
- 当前状态：第一批可复用接口与基础实现；生产告警、持久化评测集和完整链路埋点仍待完成

## 1. Trace 与 Harness

`ObservedHarnessAdapter` 以装饰器方式包装现有 HarnessAdapter，不替换 Strands、LangGraph 或其他 Harness 执行引擎。Trace 元数据统一关联：

- tenant_id、run_id、agent_id、correlation_id；
- 模型模板 ID 与版本；
- 输入/指令长度、输出长度、工具调用数；
- input/output tokens、finish_reason、运行状态和耗时。

默认不采集原始 Prompt 和模型输出。只有显式设置 `capture_content=True` 才采集截断后的内容；采集前递归脱敏敏感键、Bearer Token 和常见 API Key。Langfuse 适配器使用 SDK v3 的 `get_client()` 与 `start_as_current_observation(as_type="span")`，通过环境变量配置自托管地址和密钥。

生产启动时应显式初始化 Langfuse 环境变量，应用关闭时调用 SDK flush/shutdown，避免进程退出前丢失缓冲 Trace。

## 2. 指标

当前指标：
- `agent_runs_total`：按组件、模型模板、状态计数；
- `agent_run_duration_seconds`：运行耗时；
- `agent_input_tokens_total` / `agent_output_tokens_total`：Token 计数；
- `agent_tool_calls_total`：工具调用次数。

`InMemoryMetricsRecorder` 提供线程安全的测试/单进程实现和 Prometheus 文本格式导出。标签仅允许有限枚举字段，不允许 tenant_id、run_id、user_id 等高基数字段。

该实现尚未提供 HTTP /metrics endpoint、Prometheus 服务发现、告警规则、通知渠道或队列积压指标。生产环境应将 MetricsPort 替换为 Prometheus/OpenTelemetry exporter，并在 Gateway、模型代理、RAG、事件消费者和审批服务中逐一埋点。

## 3. Agent / RAG 评测

`evaluation.py` 提供可复现的版本化数据集结构、确定性规则评测和发布门禁：
- Agent 输出必含词、禁用词检查；
- RAG Recall@K 与 Reciprocal Rank；
- 汇总通过率、平均分和平均召回率；
- 按阈值生成阻断原因。

当前是确定性基线，不以模型自评替代事实评测。后续需要把真实回归集、人工评分、模型对比结果持久化，并补充引用正确性、事实一致性、拒答、安全策略、延迟和成本等评测维度。

## 4. Prompt 版本

`PromptRegistry` 当前提供：
- 内容哈希和不可变版本号；
- 占位变量自动解析与严格校验；
- 显式激活版本和回滚；
- 基于 rollout_key 的确定性 Canary 百分比分流。

当前注册表为进程内实现，重启后不保留；尚未接入 PostgreSQL 持久化、审批工作流、灰度指标自动回滚和 Prompt 管理 API。生产发布前不得把内存注册表当作唯一版本事实源。

## 5. 测试

```bash
python -m unittest tests.test_observability -v
ruff check agent_platform/observability tests/test_observability.py
mypy
```

测试覆盖脱敏、默认不采集原文、Trace/指标记录、失败状态、Token 计数、低基数标签、Agent/RAG 规则评测门禁、Prompt 变量校验、Canary 与回滚。

## 6. 未完成项

- Langfuse 在真实部署环境的自托管连通性、权限隔离和数据保留策略验证；
- 将 Trace/Span 贯穿模型调用、工具、MCP、知识检索、黑板、审批和事件消费；
- Prometheus/OpenTelemetry exporter、/metrics endpoint、告警聚合/抑制、通知渠道和处理闭环；
- 真实 Agent/RAG 回归数据集、人工评分、模型对比和发布门禁；
- Prompt 版本持久化、审批、灰度、自动回滚和变更审计。

未完成上述项目之前，不得将 #16 标记为完成。
