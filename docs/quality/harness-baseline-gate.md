# Harness 质量与成本基线门禁（C-21-04）

`evaluate_harness_baseline` 将同一固定任务集的报告转换为可机读的 release decision。默认门槛为：eligible case pass rate ≥ 95%、平均质量锚点 ≥ 0.95、p95 延迟 ≤ 30 秒、跳过比例 ≤ 25%，且至少有一个 eligible case。

调用方可按模型/环境覆盖门槛，并可设置每个 eligible case 的平均成本上限。设置成本上限时，所有 eligible observation 必须包含显式定价结果；缺失定价会 fail closed。无样本、样本不足、质量/通过率/延迟/跳过率越界均拒绝通过。

报告输出 checks、实际 metrics、baseline 和 limitations，便于归档及 CI 消费。p95 使用 nearest-rank 计算。基准结果必须标明候选 runtime、固定 suite 版本、模型模板/价格快照和执行时间。

## 范围限制

现有 Harness PoC 顺序执行固定样本；本门禁不把顺序延迟伪称并发容量测试。并发吞吐、饱和点、错误率和长时间 soak 仍需在隔离环境运行真实负载后单独验收。质量分数是确定性 anchor 命中率，不替代人工评审或 RAG gold-set 评测。真实 Provider 成本只有传入有效 token pricing 才可判定。
