# Prompt 版本运营与评测门禁

Prompt Registry 的不可变版本、模板变量提取、租户命名空间和确定性灰度分流由既有 registry/store 提供。本任务增加独立运营端点：

- `POST /api/platform/v1/prompts/{prompt_id}/test`：指定不可变版本进行渲染测试；只记录版本号、内容哈希和变量名，不将变量值或渲染内容写入审计。
- `POST /api/platform/v1/prompts/{prompt_id}/release`：发布前调用服务端 `evaluate_candidate(tenant_id, prompt_id, version)`。只有返回 `passed=true` 且包含非空 `evidence_id` 才允许发布；`canary_percentage=0` 激活稳定版本，1–100 设置灰度比例。
- `POST /api/platform/v1/prompts/{prompt_id}/rollback`：将活动指针恢复到指定历史版本；版本记录本身不可修改。

三个端点均使用已认证 Principal 的 tenant/user 身份，并通过 Prompt 管理授权器检查 `test` / `publish` 动作。发布与回滚在变更前写入审计；审计服务不可用时 fail-closed，不执行变更。发布审计包含评测证据 ID、版本和内容哈希，不记录 Prompt 正文、变量值或渲染结果。

生产组合根通过 `prompt_evaluation_gate`、`resource_audit_writer` 和既有 `prompt_registry` 注入依赖。评测 gate 必须由服务端绑定固定阈值策略和候选评测报告，不能直接信任客户端提供的策略或 pass 标志。当前实现不执行真实模型调用、线上流量或生产灰度验证；这些属于部署后的现场验收。
