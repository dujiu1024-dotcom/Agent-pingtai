# At-least-once 事件副作用幂等契约

Redis Streams Consumer 使用至少一次投递。若 Worker 在外部副作用成功后、Receipt 完成前崩溃，事件会在租约过期后再次执行。因此 Receipt 去重只能避免并发重复处理，不能单独保证远端副作用恰好一次。

## 统一约定

- 使用 `IdempotentEventHandler` 包装业务 handler。
- 下游执行器必须实现 `IdempotentEventEffectExecutor.execute(idempotency_key, event)`。
- 稳定键格式为 `tenant:<tenant_id>:event:<canonical_uuid>`，不同租户不会发生键冲突。
- 下游必须在产生副作用前持久化/原子化去重该键，并在重复请求时返回原结果；仅接收参数但不真正去重不满足契约。
- 如果外部系统不支持幂等键，必须通过本地事务 Outbox、对账补偿或可查询的幂等账本封装，不得宣称 exactly-once。

## 故障窗口

1. Consumer claim receipt。
2. Handler 将稳定幂等键交给下游。
3. 下游原子去重并执行副作用。
4. Consumer complete receipt。

若在步骤 3 与 4 之间崩溃，重放仍会使用同一键，下游返回既有结果，不重复执行副作用。

## 验收

自动化测试验证同一 tenant/event 的重复交付只产生一次下游副作用，不同 tenant 的相同 event UUID 使用不同键，非法身份在执行前被拒绝。真实外部服务仍需验证其幂等键语义和保留期限。
