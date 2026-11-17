# C-19-01：旧系统资产盘点

本能力生成可复核的旧系统资产清单，覆盖旧运行时代码/配置文件、SQLite 表结构与数据量、身份关系线索、业务映射候选和迁移风险。它是只读盘点工具，不执行迁移、不修改源库，也不宣称映射已完成验收。

## 执行

```bash
python -m scripts.inventory_legacy_system --root .
python -m scripts.inventory_legacy_system --root . --db /path/to/agency.db --output /secure/path/legacy-inventory.json
```

默认数据库路径为 `agency/state/agency.db`。数据库不存在时，报告将 `status` 标记为 `not_found`，行数保持 `null`（未知），不会伪报为 0。输出文件必须是尚不存在的新文件。

## 报告结构

- `files`：已知旧系统根目录下的文件路径、字节数、类别和风险。仅读取文件系统元数据，不读取文件内容。
- `database.tables`：SQLite 表、列、主键顺序、外键、精确行数、身份/业务列名、敏感列名，以及每个索引的唯一性、来源、部分索引标志、列顺序、排序方向、collation 和表达式列；另列出视图、触发器、索引总数及数据库文件大小。
- `identity_relations`：根据列名和外键推断的身份关联线索；不输出原始用户 ID、open_id、token 或业务行。
- `business_mappings`：旧项目、任务、用户/会话、客户/财务、知识资产及连接器等到新平台能力的候选映射。除需领域设计的财务映射外，均标为 `candidate_unverified`；需要业务负责人确认语义、所有权和租户边界。
- `migration_candidates`：代码/能力迁移决策目录；`reuse/adapt/replace/retire/unassessed` 是决策建议，不等同于数据迁移完成。
- `risk_summary`：按 `critical/high/medium/low` 汇总资产与表风险；敏感凭据列为 critical，身份/批量数据库数据为 high。
- `limitations`：明确缺失数据库、元数据扫描边界和人工确认项。

## 安全与解释边界

- SQLite 使用 `mode=ro` 和 `PRAGMA query_only=ON`；只读取 schema、外键和 `COUNT(*)`，不读取业务行值。
- 文件盘点只记录元数据，不计算内容哈希；隐藏目录和新平台 `agent_platform/` 不纳入旧系统文件扫描。
- 敏感列只输出列名与风险，不输出单元格内容。
- 身份关系是基于 schema 名称/外键的线索，不替代人工身份核验；业务映射均为未验证候选。
- 实际生产数据库、对象存储、飞书租户和外部数据源需在有权限的环境传入并盘点；仓库缺失数据不能推断其体量或状态。

## 验证

```bash
python -m unittest tests.test_legacy_inventory -v
```
