# C-72-03 超限上下文策略

本策略在 C-72-02 Token 估算与模型上下文窗口预检之后、Provider dispatch 之前执行。每个 primary/fallback 模型候选都按该候选自己的固定模型 ID、tokenizer 和上下文窗口重新估算。未知 tokenizer、编码错误、估算结果无效或策略处理后仍超窗时，一律 fail-closed。

## 支持策略

| 策略 | 行为 | 安全边界 |
|---|---|---|
| `reject` | 默认拒绝超限请求 | 不改写消息，不调用 Provider |
| `truncate` | 保留 system 指令、最新非 system 消息和受保护上下文；从较旧对话开始裁剪 | 不裁剪 system 指令、最新任务消息或受保护锚点；核心内容本身超窗则拒绝 |
| `summarize` | 调用宿主注入的可信摘要器压缩历史 | 必须注入摘要器；摘要后重新估算；摘要结果超窗则拒绝 |
| `human_intervention` | 将超限上下文交给宿主注入的人工审批处理器 | 必须有人工处理器并返回明确批准后的消息；无人处理或审批结果超窗则拒绝 |

## 必须保留的 Run 锚点

所有非 reject 策略都必须传入 `protected_context`，且包含以下四个字段：

- `goal`：关键目标
- `constraints`：约束
- `decisions`：已确认决策
- `evidence`：证据及其来源标识

四项被规范化为独立的受保护 system 消息，随裁剪/摘要/人工审批后的上下文一并发送。任何字段缺失、不可序列化或超出安全长度均拒绝处理。摘要器和人工处理器同时收到原始消息与锚点；它们不能替换锚点。

## 宿主接入契约

- `OpenAICompatibleModelRouter(context_overflow_policy=...)` 选择策略，默认 `reject`。
- `summarize` 需要注入 `context_summarizer`；`human_intervention` 需要注入 `context_intervention_handler`。建议人工处理器接入现有持久化 Run Intervention/暂停恢复流程，不得将“无操作员响应”视为批准。
- 调用方通过 `protected_context={goal, constraints, decisions, evidence}` 提供权威锚点。锚点不应从自由文本中自动猜测。
- 策略处理后的消息会重新 Token 估算；配额预留和 Provider transport 使用同一份处理后消息及估算结果。
- 成功应用非拒绝策略会写入审计事件 `model.context_overflow_resolved`，记录策略、裁剪数量和 Token 计数，不记录消息正文。
- 未知 tokenizer/估算失败仍然拒绝；不会通过裁剪、摘要或 fallback 绕过 Tokenizer 失败。

## 验证边界

单测覆盖四种策略、锚点保留、摘要/人工处理器缺失、处理后仍超窗、未知 tokenizer fail-closed，以及 Router dispatch 前的裁剪和人工介入阻断。真实服务器、真实 Provider、生产数据库和真机验证不在本任务范围。
