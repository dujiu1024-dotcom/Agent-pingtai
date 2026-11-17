# 最终发布验收与 Release Gate

## 自动化门禁与生产结论分离

`platform-release-gate` 在 PR/main 上执行 E2E、安全、PostgreSQL/Redis 集成、性能 smoke、备份恢复契约和静态检查。CI 通过只代表仓库自动化检查通过，不代表生产发布获批。

## Release evidence v2（fail-closed）

复制 [RELEASE_EVIDENCE_TEMPLATE.json](RELEASE_EVIDENCE_TEMPLATE.json)，每次候选发布独立归档。验证命令：

```bash
python scripts/release_gate.py --evidence path/to/release-evidence.json --commit-sha "$(git rev-parse HEAD)"
```

必须同时提供：release_id、production 环境、候选 commit SHA、UTC 生成时间、独立的 release_owner/release_approver、不可变 evidence_archive_ref 及其 `evidence_archive_sha256`（`sha256:` + 64 位小写十六进制）、九类检查及责任人/UTC 时间/证据引用、空 blockers 和 release_decision=go。证据默认 7 天有效；未来时间、过期、占位值、缺项、失败或 SHA 不匹配均阻断。

### 九类必需检查

- `deployment_preflight`：目标环境与配置校验、依赖健康、制品 SHA-256、变更窗口批准、回滚就绪；全部显式通过。
- `e2e`：核心业务端到端验收。
- `fault_injection`：Worker、Redis、PostgreSQL、模型及对象存储故障恢复。
- `performance`：并发、延迟、成本和质量基线。
- `security`：租户隔离、越权、Prompt 注入、工具滥用和沙箱边界。
- `backup_restore`：加密备份、校验、隔离恢复及数据核对。
- `production_smoke`：生产部署后冒烟与观察窗口。
- `migration`：必须附迁移预检证据，`preflight_passed=true`、`compatibility_window_confirmed=true`，并明确 expand/contract 或 forward-fix 策略。
- `rollback`：必须附回滚演练证据，`drill_passed=true`，策略限于补偿迁移、备份恢复或 forward-fix；禁止把未经验证的 destructive down migration 作为回退。

每项 owner 是执行责任人；release_owner 对发布协调负责；release_approver 必须独立于 release_owner。每项证据及最终 JSON 应写入不可变归档；同时计算归档内容 SHA-256，分别填写 `evidence_archive_ref` 和 `evidence_archive_sha256`。验证器校验摘要格式并将其作为归档完整性核对值；实际下载归档并重算摘要仍由归档审计流程执行。

## 阻断条件与发布结论

任一检查不是 passed、责任人/证据缺失、迁移预检或兼容窗口未确认、回滚演练未通过、存在 blocker、审批人不独立、证据过期或候选 SHA 不一致，结论必须为 no-go。只有全部条件满足且显式审批后才可填写 go。验证器仅接受 go；模板默认 no-go。

## 现场边界

本仓库验证器不连接生产、不执行迁移/回滚、不切流量。真实备份恢复、迁移演练、生产冒烟、供应商/对象存储验证及观察窗口必须由授权责任人在目标环境执行并归档。本次开发不申请服务器或外部资源；未执行的真机/生产项必须保持 blocker，不能用 mock 或 CI 替代。


## Generated production acceptance report

Use `scripts/release_acceptance_report.py` to render an auditable Markdown
summary from the schema-v2 evidence document. The report lists all nine checks,
per-check owner/timestamp/evidence references, deployment preflight, migration
and rollback controls, blockers, validation errors, and a computed GO/NO-GO.
It always emits NO-GO when evidence validation fails and exits non-zero:

```bash
python scripts/release_acceptance_report.py \\
  --evidence release-evidence.json \\
  --output release-acceptance-report.md \\
  --commit-sha "$(git rev-parse HEAD)"
```

See [ACCEPTANCE_REPORT.md](ACCEPTANCE_REPORT.md) for report contents and
[PRODUCTION_RELEASE_ACCEPTANCE.md](PRODUCTION_RELEASE_ACCEPTANCE.md) for the
production sign-off procedure. Report generation is offline and does not
perform production operations.
