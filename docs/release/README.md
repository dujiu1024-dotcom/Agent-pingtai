# 最终发布验收

## 自动化门禁

`platform-release-gate` 在 PR 和 main push 上执行 E2E、安全回归、PostgreSQL/Redis 集成、性能 smoke、备份/恢复脚本契约和静态检查。相关作业通过只证明仓库内自动化检查通过，不代表生产环境验收已经完成。

## 生产证据（fail-closed）

生产发布必须提供完整证据文件，模板见 [RELEASE_EVIDENCE_TEMPLATE.json](RELEASE_EVIDENCE_TEMPLATE.json)。九类证据均为必需项（证据 schema v2）：

- `deployment_preflight`：目标环境、配置/密钥引用、依赖健康、镜像/制品摘要、变更窗口及回滚就绪检查
- `e2e`：核心用户旅程端到端报告
- `fault_injection`：Worker、Redis、PostgreSQL、模型及对象存储故障注入与恢复记录
- `performance`：并发、延迟、成本和资源基线
- `security`：租户隔离、越权、Prompt 注入、工具滥用和沙箱边界回归
- `backup_restore`：加密备份、校验及隔离环境恢复演练
- `migration`：迁移前后兼容性、锁/顺序、数据校验和耗时记录
- `rollback`：回滚触发条件、回退制品/迁移策略及恢复演练证据
- `production_smoke`：目标生产环境部署后冒烟验收

每项必须填写 `status=passed`、责任人、UTC 验证时间和可审计的证据引用。顶层 `release_owner` 必须是实际承担发布责任的人员/团队，且 `release_decision` 必须在全部证据核验后显式设为 `go`；模板默认 `pending`，不能直接用于放行。缺项、失败、时间戳不规范、证据超过 7 天或 commit SHA 与候选版本不一致，均阻断发布。

本地验证：

```bash
python scripts/release_gate.py --evidence path/to/release-evidence.json --commit-sha "$(git rev-parse HEAD)"
```

GitHub Actions 的 `workflow_dispatch` 生产验收入口要求传入 `release_evidence` JSON；不提供有效证据时作业失败。不得把模板中的占位值改成 passed 来替代真实演练。

## 人工/生产环境边界

CI 无法替代真实生产凭据、真实模型供应商、对象存储故障演练、实际备份恢复、生产流量性能验证及发布后观察。上述证据必须由执行人上传并对结果负责；在完成前，总控 #21 和工作包 G 保持开放。
