# 记忆提取与候选生命周期（C-11-02）

## 范围

本契约覆盖来源追踪、置信度、候选审核、冲突解决、纠正/遗忘、过期和归档。它复用 `agent_platform.memory.service.MemoryService` 与 `MemoryExtractionService`，不定义独立存储，也不承担 C-11-03 的版本检索策略、C-11-04 的知识库/Blackboard 集成或 C-11-05 的派生索引删除编排。

## 来源与提取约束

- 提取器只接收单次 `MemoryTurn`，不接收完整对话 transcript；输入长度上限 20,000 字符，每个 turn 最多生成 50 个候选。
- 每个候选必须携带非空 `source_ref`，用于定位原始会话轮次/来源；来源引用与候选内容、置信度、提取索引及 Agent/Project 上下文一同保存。
- 提取器输出始终先进入 `CANDIDATE` 或 `CONFLICT`，不得直接激活。候选未审核前不能被 `retrieve` 返回。
- `confidence` 是有限的 0～1 数值；它表达提取器对候选的估计，不是授权凭据，也不能替代人工/授权审核。低置信度仍只作为候选，审核者可拒绝。
- 同一 tenant、来源引用和提取序号生成稳定候选 ID；重复投递同一 turn 幂等复用原候选，不覆盖已经审核或纠正的记录。

## 状态机

| 当前状态 | 操作 | 结果 | 说明 |
|---|---|---|---|
| CANDIDATE | 授权确认 | ACTIVE | 记录审核人；只有 ACTIVE 可召回 |
| CANDIDATE | 授权拒绝 | DELETED | 写入删除标记并触发派生数据清理 |
| ACTIVE | 同范围同冲突键的新内容 | 新候选 CONFLICT | 不覆盖当前有效记忆 |
| CONFLICT / ACTIVE | 授权冲突解决 | 选中项 ACTIVE，其余 SUPERSEDED | 按 tenant、scope、scope_id 分组，不跨范围覆盖 |
| ACTIVE | 授权纠正 | ACTIVE 新版本 | 保留来源引用、更新内容/置信度并递增版本 |
| ACTIVE | 到达 expires_at | EXPIRED | 不再召回；触发派生数据清理 |
| ACTIVE | 授权归档 | ARCHIVED | 不再召回；触发派生数据清理 |
| ACTIVE / 其他可删除状态 | 授权遗忘 | DELETED | 保留审计 tombstone；清理可重试 |

## 授权边界

- 确认和拒绝候选必须传入经过认证的 `MemoryPrincipal`。USER 仅允许本人；AGENT/PROJECT 需要对应资源管理授权；TENANT 需要 `memory.manage`。
- 冲突解决、纠正、归档和遗忘沿用同一范围授权规则。不能仅凭调用方提供的 tenant/user 字符串确认候选。
- 所有记录先按 tenant 定位；不同 tenant 的记录不会进入同一候选或冲突集合。

## 重试与一致性

归档、过期、拒绝、遗忘及冲突淘汰先完成状态转换，再调用幂等清理回调。清理回调失败时，状态仍保持不可召回；重复执行相同操作会再次触发清理。冲突解决重试时必须包含已标记为 SUPERSEDED 的冲突成员，以修复前次清理失败留下的派生数据。

## 验收边界

本任务以服务层状态转换、来源/置信度校验、权限隔离和清理重试测试作为自动化证据。真实向量库/缓存/异步删除链路的端到端运行环境不在本次本地验证范围内；外部生产资源和真机验证按调度要求跳过。
