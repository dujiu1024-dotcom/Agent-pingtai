# C-19-02：可恢复迁移批次与工具

本工具提供 SQLite 源库到已建表 SQLite 目标库的可执行迁移骨架。每个批次使用稳定的 `batch_id`，以目标库内的批次表和逐行账本保存进度。目标数据写入、逐行幂等记录与游标推进处于同一个事务；崩溃后重新执行相同批次会从已提交游标继续。

## 配置

映射文件是 JSON 数组，明确每个源表、目标表和列映射：

```json
[
  {
    "source_table": "old_users",
    "target_table": "users",
    "columns": {
      "id": "id",
      "display_name": "name",
      "active": "active"
    },
    "transforms": {"name": "strip", "active": "bool"}
  }
]
```

目标表必须事先创建，且源/目标都必须具有单列主键；复合主键暂不支持，工具会明确拒绝，不会猜测键。列映射是显式转换契约；需要领域级格式转换时，应先在上游生成规范化暂存表，再将暂存表映射到目标表。工具不会擅自改变业务语义。

## 执行与恢复

```bash
python -m scripts.run_legacy_migration --source ./legacy.db --target ./target.db \
  --mapping ./mapping.json --batch-id cutover-2026-01 --batch-size 500 --max-chunks 2
# 同一 batch-id 再次执行即从持久化游标继续
python -m scripts.run_legacy_migration --source ./legacy.db --target ./target.db \
  --mapping ./mapping.json --batch-id cutover-2026-01
python -m scripts.run_legacy_migration --source ./legacy.db --target ./target.db \
  --mapping ./mapping.json --batch-id cutover-2026-01 --action status
python -m scripts.run_legacy_migration --source ./legacy.db --target ./target.db \
  --mapping ./mapping.json --batch-id cutover-2026-01 --action diff
```

## 批次状态和可靠性

- `pending`：批次已登记，尚未开始应用。
- `running`：正在处理一个或多个分块。
- `paused`：达到 `--max-chunks`，可继续执行。
- `completed`：映射的源表已抽取完毕。
- `failed`：执行发生错误；重新执行同一批次会基于已提交游标重试。
- 同一 `batch_id` 绑定源路径与映射摘要；不同配置复用 ID 会拒绝。
- 目标行使用主键 UPSERT；每行迁移账本以 batch/table/source-PK 唯一约束防重复。
- 每个分块中目标行、逐行账本和游标原子提交；未提交分块会整体回滚。
- `diff` 报告按源行比较目标数据，分别统计缺失、内容不一致、匹配数量，不输出业务字段值。
- 源 SQLite 以只读 URI 和 `query_only` 打开；目标库需由操作者预先备份并提供可写路径。

## 边界与限制

- 当前适配器是 SQLite→SQLite，目标库需预建表；PostgreSQL 目标适配器、跨数据库分布式事务和在线双写不在本任务实现范围。
- 单列主键是当前断点续传游标约束；复合主键和无主键表拒绝执行。
- 映射支持显式列重命名/筛选，以及 `strip/lower/upper/int/float/bool/json` 白名单转换；不会自动推断类型、枚举或业务语义。
- 不删除目标端多余行；差异报告只比较源端存在的记录。删除传播、双写/灰度切换和回滚策略属于后续独立任务。
- 不连接生产服务、不读取生产凭据、不访问外部资源；所有验证使用临时 SQLite fixture。
