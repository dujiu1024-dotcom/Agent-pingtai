# S-665-03 故障演练与降级路径证据

## 覆盖范围

本任务对应 Issue #665 的故障演练条目（Issue 正文编号为 S-32-03；调度编号为 S-665-03）：

- Redis 不可用：发布操作失败必须被识别；熔断器打开后不得继续调用 Redis；可选的运行事件允许退化到进程内 SSE replay，并明确标注该路径不是跨进程持久化。
- PostgreSQL 主库不可用/切换：配额存储错误必须在 Harness/provider dispatch 前向上传播；模拟 standby 提升后，新的配额预留和执行恢复。
- Vault 不可用：KV 读取不得把服务不可用误报成 secret 不存在，也不得返回缓存/空值冒充成功；HTTP 503 和连接故障均 fail closed。
- resilience、quotas、streaming 三个模块通过同一故障演练套件关联验证。

## 自动化验证

运行：

```bash
python -m unittest tests.test_issue_665_s_665_03_fault_drill -v
ruff check tests/test_issue_665_s_665_03_fault_drill.py
```

测试文件：`tests/test_issue_665_s_665_03_fault_drill.py`。

## 证据边界与现场缺口

本套件是确定性 adapter-boundary fault injection，不是实际 Redis/PG/Vault 进程终止或集群切换：

- Redis 使用会抛出连接错误的客户端边界替身；SSE fallback 使用当前进程内的 `RunEventBroker`，不构成跨 worker durable streaming 保证。
- PostgreSQL 使用真实 PostgresQuotaStore + 可控 connection factory/SQL 响应夹具，验证主库连接失败时 quota gate 阻断 provider dispatch、standby endpoint 提升后恢复；未操作真实 PostgreSQL HA 集群，也未验证复制延迟、选主、连接池 DNS/endpoint 刷新。
- Vault 使用请求函数注入连接失败和 HTTP 503；未访问真实 Vault、未轮换真实 AppRole 凭据。

按调度要求不申请服务器或外部资源。以上现场项保留为生产部署前人工演练，不以本套件替代。
