# C-15-08：最小权限运行时逐调用校验

## 契约

`LeastPrivilegeRuntimeGuard` 在每次受保护的运行时调用前向可信授权器重新查询当前策略，不缓存 allow 结果。授权输入绑定 tenant、Run、actor、resource type/id 与精确 action；调用方不能传入 allow 标记或复用旧决策。

- 授权器异常、返回类型错误或显式拒绝均 fail-closed。
- 拒绝路径写入脱敏审计，并对绑定的 tenant/Run 发起中断；审计故障不允许继续执行。
- 允许路径必须先成功写入 authorized write-ahead audit，再调用 handler。
- handler 成功/失败均记录结果审计；不记录参数、凭据、Prompt 或业务正文。
- 每次调用重新校验，策略撤销/收紧会在下一次调用生效。

## 生产接线边界

生产装配必须注入可信的授权服务、append-only audit writer 和具备 tenant/Run fencing 的中断回调。此模块提供执行门禁契约，不自行伪造数据库授权结果，也不把测试 fake 当作生产依赖。对不经过该 guard 的任意 Python/网络/文件副作用仍需由 Gateway 与 OCI 沙箱约束。

## 验证

`python -m unittest tests.test_runtime_permission_guard -v` 覆盖逐次复查、权限撤销、拒绝审计、中断、授权服务不可用、审计不可用阻断副作用及结果审计。
