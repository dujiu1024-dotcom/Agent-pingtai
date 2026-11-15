# 配额用量报告 API 增量

新增租户范围的用量查询与 CSV 导出：

- `GET /api/platform/v1/quotas/usage`
- `GET /api/platform/v1/quotas/usage.csv`

查询参数支持开始/结束时间、Run、模型和条数上限。租户 ID 仅取自认证主体，不接受调用方传入租户过滤值。时间必须带时区、结束时间晚于开始时间，查询跨度最多 366 天，单次最多 10,000 条。返回字段包含模型、Token、成本、项目、Agent、工具调用、运行时长、存储量、价格卡版本、成本来源和发生时间。

路由通过平台组合工厂的 `quota_store` 显式挂载。没有配置时不会声明或伪造该能力。

## 当前边界

现有 QuotaService、QuotaEnforcedHarnessAdapter、PostgreSQL 原子 reserve/settle/release 函数和成本账本仍需由生产执行入口统一接线。项目/Agent 多维度同时扣减、Worker 派发全路径强制配额、工具/存储实际用量采集、账单导入和差异对账仍未完成。此报告 API 不代表这些能力已经完成。

部署/真机/并发演练跳过，标注「待用户真机验证」。
