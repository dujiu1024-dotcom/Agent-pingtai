# Harness 候选对比 PoC

## 范围与候选

本 PoC 复用统一 `HarnessAdapter` 契约，对比两个已存在的候选实现：

- `DeepSeekHarnessAdapter`：DeepSeek Harness SDK；当前声明不支持流式、可靠取消、恢复和 Tool Gateway。
- `StrandsAgentsAdapter`：Strands Agents；通过受控模型代理配置，可声明流式和 run-scoped MCP Tool Gateway。

固定任务集定义在 `agent_platform/runtime/harness_benchmark_suite.py`，包含事实抽取、证据定位、算术推理、指令遵循、长上下文召回和流式输出六项。相同 case 的指令和输入在每个候选间保持一致；执行按候选顺序串行，避免并发争用影响延迟。

## 指标与语义

- **质量**：expected anchor 命中率及 case pass rate。它是可复现的筛查指标，不等同于人工偏好或事实正确性的完整评测。
- **延迟**：每个 case 的端到端耗时；流式 case 另记录首 token 延迟和事件数。
- **成本**：输入/输出 token 用量；只有调用方传入显式每百万 token 定价时才估算 USD，不猜测价格。
- **超时**：按 case 的 timeout_seconds 使用 asyncio.wait_for；异常被记录，不中断后续 case。
- **恢复**：可配置有限 recovery_attempts，记录 attempt 数和 recovered 标志。仅对可安全重试的请求启用。
- **流式**：只对声明 streaming 的候选执行流式 case；不支持的候选以 capability scorecard 明确标注，不伪造失败或流式成功。
- **取消**：scorecard 输出 adapter 声明的 cancellation 能力。声明能力不是进程终止证据；需另行对真实 SDK 子进程做取消探针，核验取消后无残留工作。
- **工具调用**：平台工具只能通过 run-scoped Tool Gateway。带 allowed_tools 的请求必须提供 gateway_tool_verifier 并验证通过；不能将模型文本中提到工具名称视为真实工具调用。

## 执行入口

`run_fixed_harness_benchmark` 接收调用方构造的候选 adapter、request_factory、可选 pricing、Gateway verifier 和有限恢复次数，返回能力 scorecard 与结构化报告。报告可通过 `benchmark_evidence_payload` 序列化为 JSON-compatible dict；原始输出仅用于受控 PoC，生产评测归档前应执行敏感信息清理。

Provider-backed 执行必须由隔离的实验调用方显式构造 adapter，并通过现有受控模型代理和已审查的运行环境注入凭据。密钥不得进入模板、仓库、报告或日志。DeepSeek Harness 仍要求调用方证明容器文件系统及网络隔离；本 PoC 不将其 read-only 模式视为安全边界。

## 当前自动化证据与限制

CI 使用确定性 adapter 测试固定任务集、候选能力差异、流式筛选和报告结构，不访问外部模型服务、不需要 API Key。此测试验证比较框架，不是模型质量/成本/延迟的真实测量。

本次调度明确跳过真机和真实 Provider 验证，也不申请服务器、代理、密钥或其他外部资源。因此不能据此宣称 DeepSeek 与 Strands 的真实模型胜负、美元成本或生产取消可靠性已测定。需要真实模型结果时，应在已批准的隔离环境中运行同一固定任务集，并将实际模型 ID、模板版本、价格快照、时间戳和完整报告作为独立证据回填。
