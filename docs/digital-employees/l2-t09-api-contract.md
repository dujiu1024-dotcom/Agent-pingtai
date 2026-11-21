# L2-T09 数字员工目录与 AI 团队模板 API 契约

## 已实现的 API 契约

- GET /api/platform/v1/digital-employees：关键词、岗位、业务目标、标签筛选与分页。
- GET /api/platform/v1/digital-employees/{employee_id}：产品身份详情，与底层 Agent 定义分离。
- POST /api/platform/v1/digital-employees/{employee_id}/install：企业/项目/团队范围安装，固定 Agent 版本，指定负责人/监督人和权限。
- GET /api/platform/v1/team-templates：按关键词/业务目标发现团队模板。
- GET /api/platform/v1/team-templates/{template_id}：模板成员岗位和协作定义详情。
- POST /api/platform/v1/team-templates/{template_id}/install：按成员覆盖岗位并配置成员权限。

## 安全边界

所有读/安装请求均要求已认证 Principal，并调用显式 read/install authorizer。tenant_id、actor_id 从 Principal 注入，不接受请求体覆盖。安装权限必须是数字员工已声明 capability 的子集；团队安装逐成员校验，越界或未知成员 fail closed。

## 集成边界

Router 仅在宿主注入 tenant-scoped catalog/install service 与授权器后挂载。Adapter 负责持久化、审计、固定版本 Agent 绑定及调用现有 Agent Team/Workflow Runtime。本任务不复制 Agent Registry、RBAC、凭据、沙箱和运行时底座。公共目录不是已安装员工运行时依赖。

## 自动化验证

运行：python -m pytest tests/test_digital_employee_api.py -q

覆盖岗位/目标发现、tenant/actor 传递、越权权限拒绝、团队成员引用校验和逐成员权限边界。真机/生产环境验证按要求跳过。
