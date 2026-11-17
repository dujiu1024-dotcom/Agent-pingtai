# C-72-02 模型调用前 Token 估算与上下文窗口检查

## 执行边界

本说明只覆盖 Issue #72 / C-72-02：模型请求进入 Provider transport 前，使用 Run 固定模型配置估算输入 Token，并将计划输出 Token 一并计入模型上下文窗口。预算定义、超限处置、预算结算和子 Run 继承分别由相邻任务负责。

## 前置检查

1. 从不可变 Run registry snapshot 解析模型模板，使用模板固定的 `model_id` 与 `context_window_tokens`，不根据请求参数选择 tokenizer 或切换模型。
2. 估算器收到最终有效请求参数：模型模板参数作为默认值，单次请求参数覆盖同名项。这与 HTTP transport 的参数合并顺序保持一致。
3. 估算器必须能解析所选模型的 tokenizer，并对消息文本、工具调用元数据及明确的输出 Token 预留进行估算。
4. 输入估算与输出预留之和不得超过 Run 固定模型的上下文窗口。

## Fail-closed 规则

- 未知模型 tokenizer、缺少 tokenizer 依赖、消息格式无法安全估算、工具元数据无法序列化或 tokenizer 编码失败：记录 preflight 拒绝/失败审计并中止请求。
- 估算结果结构非法、计数器类型非法、输出预留无效或总量超过模型上下文窗口：中止请求。
- 不因估算失败尝试 fallback 模型；fallback 也必须独立通过自己的模型/tokenizer/上下文窗口估算。
- 上述失败发生在 Provider transport 调用之前，因此不会发出 Provider 请求。
- 多模态消息在没有 Provider 专用估算器时拒绝，不用文本长度猜测图片、音频或视频 Token。

## 运行时接线

生产 Run 入口 `RegistryRuntimeAssembler` 必须检查 Model Router 配置了可调用的 `token_estimator.estimate`、QuotaService、价格估算器和审计接收器。缺少任一项时拒绝组装运行时。底层 Router 在估算失败时写入 preflight 失败审计并重新抛出估算异常；Provider transport 不会被调用。

## 验证边界

自动化测试覆盖模板默认参数与请求参数合并、固定模型上下文窗口、估算结果超窗拒绝、估算失败阻断 Provider、生产运行时拒绝不可调用估算器。真机、真实 Provider、真实服务器及生产数据库验证按调度要求跳过。
