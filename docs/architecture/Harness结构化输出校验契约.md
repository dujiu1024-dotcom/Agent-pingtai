# Harness 结构化输出契约

## 执行边界

Harness Adapter 仍负责单次受监督执行；平台通过 `SchemaValidatedHarnessAdapter` 装饰器在统一边界校验 `HarnessRequest.output_schema`，不在业务 Agent 中重复实现解析逻辑。

## 当前支持的 JSON Schema 子集

- 类型：object、array、string、number、integer、boolean、null
- 对象：required、properties、additionalProperties=false
- 数组：items、minItems、maxItems
- 字符串：minLength、maxLength
- 数值：minimum、maximum
- 枚举：enum

遇到不支持的 Schema 关键字时，不应假设它已被执行；注册中心应限制发布的 Schema 关键字集合。

## 修复重试

默认最多进行一次结构化输出修复，可配置 0–3 次。每次重试仍调用原 Harness Adapter，保留同一 Run 上下文、模型模板和允许工具集合。达到上限仍不合格时抛出 `StructuredOutputValidationError`，调用方必须将 Run 标记为失败或转人工处理，不得把无效 JSON 当作成功结果。

有效结果在元数据中附带解析后的结构化对象、尝试次数和校验状态。原始输出不应作为最终业务对象使用。

## 验收限制

本 PR 补齐 Schema 校验与有界修复契约及单元测试。DeepSeek Harness 与 Strands 的真实模型同任务质量/成本/延迟 PoC、生产沙箱隔离和多实例运行验收仍需真实环境执行；未以模拟测试替代这些验收。
