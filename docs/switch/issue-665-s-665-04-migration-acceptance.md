# S-665-04 SQLite → PostgreSQL 脱敏快照迁移验收

## 执行目标

本项按 Issue #665 的 S-32-04 要求，验证脱敏旧 SQLite 快照到 PostgreSQL 导出快照之间的三条业务链数据映射和一致性。仓库当前没有 `migrate_sqlite_to_pg.py`；本 PR 新增同名脚本，提供严格只读的 `--dry-run` 验收模式，不提供写库模式。

三条候选业务链（映射仍需对真实 PostgreSQL schema 做业务确认）：

1. 项目交付：projects → tasks → events
2. 商务财务：quotes → contracts → invoices → payments
3. 知识资产：facts → docs → embeddings

## Dry-run 方式

先从隔离的脱敏 SQLite 快照导出独立 PostgreSQL 表 JSONL 快照，再执行：

```bash
python scripts/migrate_sqlite_to_pg.py \
  --source /secure/redacted/legacy.sqlite \
  --target-snapshot-dir /secure/postgres-snapshot-jsonl \
  --mapping docs/switch/issue-665-s-665-04-mapping.json \
  --dry-run
```

输出每张表的源/目标行数、缺失/额外/变更计数和 SHA-256；不输出业务行值。返回码 0 表示映射快照一致，1 表示数据差异，2 表示输入/映射不合法。

## 自动化证据

```bash
python -m unittest tests.test_issue_665_s_665_04_migration_acceptance -v
ruff check scripts/migrate_sqlite_to_pg.py tests/test_issue_665_s_665_04_migration_acceptance.py
```

自动化测试建立仅含合成脱敏样例的临时 SQLite 和独立目标 JSONL 快照，覆盖三条业务链共 10 张表、10 行，验证一致性、差异检测、敏感列拒绝和源库字节不变。该 10 行是测试夹具，不是生产迁移数据或 PostgreSQL 实测数据。

## 证据边界

- 本工具只读打开 SQLite 源快照；不建立 PostgreSQL 连接、不执行 INSERT/UPDATE/DDL，不改变源/目标。
- 目标 JSONL 必须来自独立 PostgreSQL 快照导出；本 PR 的自动化目标文件是独立合成夹具，不代表真实 PG 数据。
- 映射文件标记为候选映射。真实表结构、租户归属、外键语义、字段变换、身份/敏感数据处理，必须在现场用脱敏快照复核。
- 本次按调度要求不申请真实数据库或外部资源，因此不宣称真实 PG 迁移已执行或 100% 一致。
