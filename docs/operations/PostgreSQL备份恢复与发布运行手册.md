# PostgreSQL 备份、恢复与发布运行手册（代码交付版）

## 目标与边界

- PostgreSQL 是平台业务事实源；Redis 用于缓存/队列传输，不作为唯一业务事实存储。
- PostgreSQL 备份使用 custom format，备份文件由 age 公钥加密，旁路生成 SHA-256 校验文件。
- 备份目录权限收紧为 0700，文件权限为 0600。
- 恢复必须显式设置确认字符串，先校验密文 checksum，再使用 age identity 解密，最后 pg_restore。

## 备份

依赖 PostgreSQL 客户端工具、age、sha256sum。

```bash
export PG_DSN='postgresql://...'
export AGE_RECIPIENT='age1...'
export BACKUP_DIR='/secure-backups/platform'
bash scripts/backup_postgres.sh
```

密钥应由部署密钥系统注入，不要写入仓库、命令历史或日志。备份目录应位于独立故障域，并配置离线/异地副本和保留策略。

## 恢复

恢复会覆盖目标库中的同名对象，必须选择隔离恢复目标并确认业务切换窗口：

```bash
export PG_DSN='postgresql://restore-target/...'
export AGE_IDENTITY='/secure-secrets/backup.agekey'
export BACKUP_FILE='/secure-backups/platform-YYYYMMDDTHHMMSSZ.dump.age'
export CONFIRM_RESTORE='RESTORE-PLATFORM-DATABASE'
bash scripts/restore_postgres.sh
```

恢复完成后由运维按发布清单执行迁移版本核对、租户/用户/运行/文件元数据抽样、向量索引重建检查、Outbox 未发布事件检查和应用健康检查。

## 升级/回滚

- 先备份并验证密文校验。
- 在预生产环境按迁移顺序应用 SQL，检查兼容窗口和索引构建。
- 采用 expand/contract：先加兼容字段/表，再部署双读校验，再切换写入方，最后移除旧结构。
- 应用版本回滚不能自动逆转破坏性数据迁移；每个迁移需提供前向修复或明确恢复步骤。
- Redis 可重建；Outbox 和数据库事件收据负责恢复可重放事件。

## 未执行的生产验收

本次不执行部署、备份恢复演练或灾备演练。RPO/RTO、异地副本恢复、对象存储备份、真实 Redis 丢失后 Outbox 重放、从空环境部署和应用升级回滚均标注「待用户真机验证」。不得在未演练前填写实际 RPO/RTO 数值。
