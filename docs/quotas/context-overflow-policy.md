# C-72-03 上下文超限策略

本模块为 Run 上下文提供显式的拒绝、截断、摘要压缩和人工介入策略。策略只作用于结构化上下文段，不从任意对话文本推断业务语义。

## 关键上下文保护

调用方必须将上下文拆分为 ContextSection，并明确标注 kind：
- goal：本次运行目标；
- constraint：不可违反的约束；
- decision：已确认决策；
- evidence：证据、来源和验证结果；
- context：可压缩的工作上下文、历史细节和辅助说明。

前四类是关键段，策略始终逐字保留；关键段本身超过有效 token 上限时直接拒绝，不会静默截断关键目标、约束、决策或证据。

## 策略行为

- reject：超限即抛出 ContextOverflowError，调用方不得继续模型派发。
- truncate：按优先级从低到高移除可选 context，关键段保持原文。
- summarize：仅把可选 context 段交给宿主提供的 summarizer，关键段不进入摘要输入；摘要必须经同一 token counter 复核后才可使用。
- human：返回 requires_human=True 和原始上下文，表示暂停并转人工处理；不是允许模型继续执行的成功结果。

## 集成契约

宿主必须注入与最终模型、tokenizer 和消息序列化一致的 token_counter。摘要器由宿主显式提供，不在本模块内隐式调用模型。ContextOverflowResult 提供保留/移除段键、前后 token 数及人工介入标志，供调用方记录 Run 事件、审计和等待审批。任何计数错误、摘要失败、关键段超限或策略配置错误均 fail-closed。

## 范围

此交付提供可测试的策略引擎与稳定集成契约；不替代 C-72-02 的模型 tokenizer/上下文预检，也不实现 C-72-04 的预算用量持久化。
