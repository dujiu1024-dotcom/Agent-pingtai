# C-15-09：运行时安全事件自动响应

## 流程

`RuntimeSecurityAutoResponder` 将处理拆成四个显式阶段：

1. **检测**：`ThresholdSecurityDetector` 将规范化运行时观测与精确的 event-type/signal 阈值规则匹配；未命中不产生副作用。
2. **判定**：`RuleBasedSecurityPolicy` 按事件类型与最低严重级别选择版本化响应规则；没有匹配规则时拒绝自动动作。
3. **动作**：`SecurityResponseExecutor` 只接受固定枚举动作（中断 Run、隔离 Run、隔离租户、撤销凭据），并且必须由部署显式注册动作处理器。
4. **审计**：决策、动作开始（write-ahead）和动作结果写入审计。动作前审计不可用时阻止动作；审计元数据经过脱敏，不记录凭据/原始载荷。

## 幂等与失败策略

- `SecurityResponseEventStore.claim_once(tenant_id,event_id)` 必须由持久化实现提供原子唯一约束；重复投递不重复执行响应。
- 授权/策略决策异常 fail-closed，不执行动作并记录决策失败。
- 动作异常记录失败结果，交由运维/后续重试策略处理；不自动重复可能非幂等的隔离/撤销动作。
- 未配置动作处理器会失败，不会动态调用任意函数。
- 每个事件携带 tenant/Run/actor 作用域，事件审计使用事件租户作为隔离边界。

## 接线边界

本 PR 提供检测、决策、执行和审计协议及可测实现，不伪造生产事件源、持久化 claim store、授权策略管理或隔离/撤销 API。生产启用前需将 claim store 接入唯一约束存储，并将动作处理器接到可信 Run/tenant 控制面；应在现场验证控制面故障、竞态和真实中断效果。
