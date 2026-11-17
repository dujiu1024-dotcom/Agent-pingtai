# A-666-05 / S-33-05 灰度切流验收

## 验收范围

本项验证已有 `agent_platform.data_governance.cutover` 控制面策略在无真实流量条件下的灰度决策：Legacy-primary → dual-write → canary，固定租户/请求键分流、10% 灰度分布、影子复制计划、观测门禁和异常回滚决策。

核心业务链的既有端到端回归由 `tests/test_core_business_journey_e2e.py` 覆盖：登录/身份认证、知识文档写入与检索、多专家工作流、审批中断与恢复、最终交付物持久化。此测试仍由仓库全量 unittest CI 执行。

## 自动化演练

```bash
python -m unittest tests.test_issue_666_a_666_05_canary_acceptance -v
python -m unittest tests.test_core_business_journey_e2e -v
ruff check tests/test_issue_666_a_666_05_canary_acceptance.py
```

新测试使用 100 个合成租户、每租户 100 个稳定请求键，共 10,000 个确定性路由样本。10% 灰度容差设为 8%–12%；每个请求重复计算以确认重试路由稳定。另验证目标系统写主、旧系统影子复制计划，以及 checksum/行差异/写错误率异常、证据过期、样本不足和观测窗口不足时拒绝切流或回滚。

## 证据边界

- 10,000 个请求是确定性路由策略测试样本，不是真实流量或生产租户。
- 本测试只验证 cutover 决策/路由计划，不实际修改 API Gateway、Workflow 入口或负载均衡器配置。
- 业务链依赖已有 E2E 测试，不代表本 PR 启动了新旧两套真实系统。
- 未连接生产/预发服务，未申请真实服务器或外部资源；实际灰度期间错误率、延迟、业务成功率及 5 分钟回切仍需现场观测与演练。

## 判定

CI 全量测试通过后，只能认定策略级自动化灰度验收通过；不能将模拟分流结果写成生产灰度切流无异常。现场证据应补充实际 tenant allowlist、切流百分比、流量窗口、请求总数、错误率/延迟、核心业务链成功率及回滚演练记录。
