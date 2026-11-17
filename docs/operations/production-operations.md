# 生产运维手册（C-18-05）

本手册覆盖部署、升级、备份、恢复、回滚、验证和故障排查。生产操作必须使用受控变更单、最小权限账号和不可变镜像摘要；不得把密钥、客户数据或原始日志提交到仓库。

## 1. 工具入口

```bash
python scripts/ops_runbook.py preflight
python scripts/ops_runbook.py plan deploy
python scripts/ops_runbook.py plan upgrade
python scripts/ops_runbook.py plan backup
python scripts/ops_runbook.py plan restore
python scripts/ops_runbook.py plan rollback
python scripts/ops_runbook.py plan verify
python scripts/ops_runbook.py plan diagnose
python -m unittest tests.test_ops_runbook -v
```

preflight 只读取本机 Python/Git/Docker/Compose 和 .env 文件权限，不读取或输出密钥值。plan 输出有序检查清单，不会启动容器、迁移数据库、切换流量或修改数据。生产变更仍须按审批执行。

## 2. 部署

1. 确认目标环境、租户/项目边界、容量、依赖服务地址和负责人。
2. 从密钥管理系统注入 Secret；禁止将 .env 或 Secret 写入镜像、日志、命令行参数和 Git。
3. 固定镜像 digest、配置版本和迁移版本；适用 Compose 部署时运行 docker compose config --quiet。
4. 执行迁移前检查与兼容性检查，确认数据库备份可用。
5. 按环境部署配置逐步发布，等待 readiness/health check；未就绪时停止扩大批次。
6. 执行第 7 节验证并记录变更单、镜像 digest、迁移结果和观测窗口。

## 3. 升级

1. 检查发布说明、依赖升级、数据库迁移、API/事件契约兼容窗口。
2. 先备份并验证清单；确认恢复责任人和回滚窗口。
3. 优先采用 expand/contract：先增加兼容字段/索引，再滚动升级应用，最后在兼容窗口结束后清理旧结构。
4. 逐批替换实例，观察错误率、延迟、队列积压、数据库连接和资源用量。
5. 迁移失败或健康指标越界时暂停发布；按第 6 节回退，不得盲目重复执行非幂等迁移。

## 4. 备份

- PostgreSQL：按 RPO 选择连续 WAL/PITR 或定期基础备份，并定期做独立恢复验证。
- Redis：明确缓存/队列/持久状态的不同恢复要求；按实际持久化配置备份 RDB/AOF，不能把缓存快照等同于队列一致性证明。
- 对象存储：启用版本控制/生命周期策略，备份关键桶清单、对象版本和元数据。
- 配置：备份非秘密配置版本；Secret 由密钥管理系统独立备份和审计。
- 每份备份记录 UTC 时间、备份 ID、源版本、组件范围、加密/保留策略、对象位置及 SHA-256。禁止把凭据写入清单。

清单 JSON 至少包含 backup_id、带时区的 created_at、source_release、artifacts（每项含唯一 name、64 位十六进制 sha256、location）以及 components 中 postgres、redis、objects、configuration 均为 true。使用工具校验：

```bash
python scripts/ops_runbook.py validate-backup /secure/backup/manifest.json
```

该校验只检查元数据结构，不会读取备份对象或重新计算实际校验和；恢复前仍须从受控存储下载/读取对象并独立核对哈希。

## 5. 恢复

1. 建立事件编号，限制写入并隔离故障组件；保留现场证据。
2. 选取同一时间窗口且校验通过的备份集，验证密钥/配置版本。
3. 按依赖顺序恢复配置与依赖、PostgreSQL、Redis、对象存储。
4. 执行跨组件引用、Outbox/事件状态、租户授权和数据完整性核对。
5. 运行应用 smoke test 和后台消费者检查；审批后逐步恢复流量。
6. 记录实测 RPO/RTO、丢失/重放数据、偏差和审批证据。灾备策略与演练记录校验见 docs/disaster-recovery-runbook.md 和 scripts/disaster_recovery.py（若对应变更已部署）。

禁止在未确认备份身份、目标环境、数据范围和恢复审批前覆盖生产数据；优先在隔离环境演练。

## 6. 回滚

1. 暂停发布和新迁移，记录当前版本与故障时间。
2. 确认上一稳定镜像 digest、配置版本及其与当前数据库 schema 的兼容性。
3. 回退应用/配置；只有经过审批且有明确补偿方案时才执行反向迁移。
4. 若 schema 已不可逆变更，采用前向修复或经过批准的备份恢复，不得直接降级应用掩盖数据不兼容。
5. 完成健康、数据一致性、队列消费和租户隔离验证后恢复流量。

## 7. 验证与故障排查

发布/恢复后检查：readiness 与依赖连通性、关键 API 契约、数据库迁移状态、队列积压与死信、对象读写、租户隔离/权限拒绝、审计事件、错误率/延迟/资源饱和度。记录检查时间、执行人、版本、结果和证据链接。

故障排查顺序：确认影响范围与最近变更 → 检查服务健康和依赖 → 检查资源/连接池/队列 → 查看脱敏日志与追踪 → 对比版本和配置 → 执行已批准的恢复或回滚流程。对数据丢失、跨租户访问、主节点脑裂、校验失败或密钥泄露立即升级安全/值班负责人；不要在日志、Issue 或 PR 中粘贴 Token、连接串或客户载荷。

## 8. 操作安全边界

- 生产命令必须由有权限的值班人员在受控终端执行，并记录审批单。
- 先 dry-run/plan，再执行；破坏性操作需要双人复核。
- 备份加密、异地/跨账号隔离、最小权限、保留到期销毁均由基础设施策略落实。
- 演练通过不等于生产恢复能力已被证明；现场证据需单独登记。
