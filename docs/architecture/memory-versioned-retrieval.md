# 记忆版本与检索契约（C-11-03）

## 数据与溯源

- 每条记忆使用稳定的 `memory_id`；每次状态或内容变更递增 `version`。
- 候选保留 `source_ref`、`confidence`、`created_at`、`updated_at`、`expires_at` 和 scope。
- 检索结果必须附带 `MemoryCitation`，精确引用 memory_id、版本、来源、scope、scope_id、置信度和时间戳；不得只返回无法追溯的文本。
- 内容纠正产生新版本并更新来源引用；历史版本只通过经过 scope 管理授权的版本历史接口访问。

## 适用范围与权限

- 检索首先按可信 principal 的 tenant/user/project/agent 身份过滤，再检查 ACTIVE 状态、删除标记和时间有效性，最后执行查询匹配与排序。
- USER 记忆仅对本人可见；PROJECT / AGENT 记忆仅对 principal 已加入的项目/Agent 可见；TENANT 记忆仅在同一 tenant 内可见。
- 版本历史使用管理权限检查，不因“能读取当前记忆”而自动开放历史审计内容。
- 不允许客户端传入 tenant_id 替代认证 principal；本服务仅接受 `MemoryPrincipal`。

## 时间与召回策略

`MemoryRecallPolicy` 提供 scope 白名单、最低置信度、结果数量上限、最大更新时间窗口、as-of 时间及来源前缀过滤。候选还可在 metadata 中携带 ISO-8601 `valid_from` / `valid_until`，并与 expires_at 一起参与时间判断。

检索采用确定性词项重叠、置信度先验和时间衰减排序。该策略是可测试的基线召回器，不声称提供语义向量检索；未来 Embedding / Rerank 可替换排序实现，但不得改变授权前置过滤与引用契约。

## 验证范围

`tests/test_memory_retrieval.py` 覆盖租户与 scope 隔离、时间窗口/有效期、来源和置信度过滤、查询召回、版本历史及未授权访问拒绝。生产持久化版本仓库、真实向量召回与真实部署配置属于后续集成/现场验收，不由本项的内存适配器测试代替。
