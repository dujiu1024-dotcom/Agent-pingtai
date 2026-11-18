# 组织与 RBAC 管理：字段/API 契约核验台账

> Issue #756 / UI-G2-756。本文区分产品字段清单与当前仓库已证实的后端契约。没有源码证据的字段、端点、状态机和授权规则不得由前端推定。

## 当前核验结论

- docs/frontend/page-map.md 冻结了 ADM-ORG → /admin/organization 与 ADM-RBAC → /admin/access-control；权限关闭时隐藏，权限启用时由 RBAC 控制。
- 当前后端组合入口 agent_platform/api/app.py 没有已证实的组织、部门、项目成员、邀请、角色、权限目录或数据范围管理 router 注册。
- 已核对的 docs/api-contract.md 只定义通用 HTTP 认证/授权、cursor 分页、错误模型等规则，没有组织/RBAC 实体 schema 或具体端点。
- #755 PR #765 仅建立启动时 auth 双模式组合基座；其说明明确表示 Principal 依赖端点仍待迁移。该 PR 不提供组织/RBAC API，也不提供可供前端消费的 bootstrap capability schema。
- 因此当前页面只呈现接入状态和后端契约缺口；不生成虚构组织、用户、角色、权限、邀请或策略，不发送猜测 API 请求，不启用会改变授权的按钮。

## 实体字段与状态机核验

| 实体 | 产品字段清单（来自 #749） | 当前真实 schema / 主键 | 状态机 / 关系 | 结论 |
|---|---|---|---|---|
| Organization | id、name、code、status、owner、member_count | 未找到实体 schema；id 仅是产品字段建议 | 与部门、项目关系未定义 | 缺 schema/API |
| Department | id、name、code、parent_id、status、owner、member_count | 未找到实体 schema；主键未证实 | parent/child 约束、循环校验、级联行为未定义 | 缺 schema/API |
| Project membership | project relation、成员归属 | 项目成员关系 schema 未找到 | 组织/部门/项目归属优先级及成员多归属未定义 | 缺 schema/API |
| Member / User | user_id、username、display_name、email（若有）、status、last_login、role_ids | Identity 登录接口不等于成员管理 schema；成员 API 未找到 | 邀请接受、停用、重新启用、删除/保留策略未定义 | 缺 schema/API |
| Invitation | 未定义稳定字段 | schema 与主键均未找到 | 创建、发送、过期、撤销、接受、重复邀请语义未定义 | 缺 schema/API |
| Role | role_id、name | role schema/API 未找到 | 系统角色/自定义角色、内置角色保护、版本并发未定义 | 缺 schema/API |
| Permission | permission_id/key、resource、action | 权限目录 schema/API 未找到 | menu/page/action/API grant 的键空间和组合语义未定义 | 缺 schema/API |
| Data scope policy | data_scope | scope 枚举、策略 schema/API 未找到 | 组织/部门/项目/个人范围继承、交集/并集、覆盖优先级未定义 | 缺 schema/API |
| Audit event | 授权变更审计要求 | RBAC 审计事件 schema 未找到 | actor、target、before/after、reason、request_id 及 append-only 约束待核验 | 缺 schema/API |

上表中 #749 的字段仅为产品字段清单，不代表数据库列、Pydantic schema 或 API 已存在。正式接入前须以代码 schema 为准补齐字段类型、必填/可空、唯一约束、索引、敏感字段、创建/更新时间、revision/ETag 和并发语义。

## API method/path 台账

| 能力 | method/path | 注册位置 | request/response schema | 授权动作 | 状态 |
|---|---|---|---|---|---|
| 组织树列表/详情/创建/更新 | 未找到 | 未找到 router 注册 | 未找到 | 未定义 | missing |
| 部门树/父子关系维护 | 未找到 | 未找到 router 注册 | 未找到 | 未定义 | missing |
| 项目成员归属 | 未找到 | 未找到 router 注册 | 未找到 | 未定义 | missing |
| 成员列表/详情/停用 | 未找到 | 未找到 router 注册 | 未找到 | 未定义 | missing |
| 成员邀请/撤销/重发 | 未找到 | 未找到 router 注册 | 未找到 | 未定义 | missing |
| 角色列表/详情/创建/更新 | 未找到 | 未找到 router 注册 | 未找到 | 未定义 | missing |
| 权限目录与角色 grants | 未找到 | 未找到 router 注册 | 未找到 | 未定义 | missing |
| 数据范围策略 | 未找到 | 未找到 router 注册 | 未找到 | 未定义 | missing |
| 管理员初始化 | #755 启动模式基础 PR 未提供组织/RBAC 初始化 endpoint | 未找到组织管理 router | 未找到 | 仅 auth=true；策略待定义 | missing |
| 授权变更审计 | 通用审计能力不等于 RBAC 事件接线 | 未找到 RBAC audit event source | 未找到 | append-only 要求待实现 | missing |

禁止基于产品期望自行发明 /organizations、/departments、/members/invitations、/roles 或 /permissions 等路径。服务端应先提交真实 OpenAPI/schema 与路由注册证据，再由前端适配。

## 模式挂载契约

| 启动模式 | 页面/导航 | API 行为 | 当前可验收状态 |
|---|---|---|---|
| auth.enabled=false | 组织、成员、角色、RBAC 导航隐藏；直达页应返回不适用/不可用说明，不得要求管理员或默认角色 | 不创建匿名用户/默认组织/默认角色；不做成员/部门/项目数据范围 ACL | #755 PR #765 已建立 app composition 模式基础；组织管理页面守卫与业务 API 尚未实现 |
| auth.enabled=true | 只有服务端 bootstrap 明确报告模式及 capability 后才显示；页面操作由服务端 RBAC 再授权 | 初始化首个管理员、成员邀请、角色授权、数据范围和审计均由服务端实现 | bootstrap capability schema 与组织/RBAC endpoints 均缺失，不能进行真实 auth=true 验收 |

当前 React shell 没有已核实的 bootstrap 元信息接口。组织/RBAC 路由已注册为深链占位，但不进入主导航，且页面只显示契约缺口，不暴露虚构管理操作。后续必须由 #755 提供稳定 bootstrap 字段（包括 auth.enabled 和 capability 状态）后再实现模式守卫；不得用前端常量或本地存储推断实例权限模式。

## 安全与验收缺口

1. 组织树：服务端必须校验 parent 是否属于同一组织、禁止循环引用、定义删除/停用子节点行为。
2. 成员邀请：token 只存哈希、一次性使用、过期/撤销、重放与重复邀请幂等语义；邮件地址仅在真实 schema 支持时展示。
3. 角色权限：权限目录来自服务端；权限预览/差异不能代替实际授权；写接口必须逐资源/动作授权。
4. 数据范围：定义 scope enum、继承规则和查询过滤策略；跨项目拒绝必须在服务端证明，不能只在前端过滤列表。
5. 管理员初始化：仅 auth=true 且未初始化时允许，禁止公开注册；初始化凭据和邀请秘密不得写入 URL/日志。
6. 审计：邀请、撤销、停用、角色 grant/revoke、数据范围策略修改均记录 actor、目标资源、变更前后摘要、原因与 request_id；审计存储 append-only。
7. 并发：成员/角色/策略修改需要 revision/ETag 或等价条件写；冲突返回稳定 409 CONFLICT / 412 PRECONDITION_FAILED，不得静默覆盖。
8. 双模式冒烟：auth=false 验证页面/导航不要求身份且不创建组织/用户/角色；auth=true 待 bootstrap 和端点完成后覆盖管理员初始化、邀请、停用、角色授权、数据范围、403、跨项目拒绝、审计和并发冲突。

## 前端交付边界

- /admin/organization 与 /admin/access-control 路由遵守 #753 冻结的 page_id/path。
- UI 当前明确显示后端缺口，不显示样例行，不提供会误导用户的创建/邀请/授权按钮。
- 未将组织/RBAC 入口加入主导航，直到真实 bootstrap 能确认 auth.enabled=true 且 capability 可用。
- 桌面/平板页面使用响应式卡片；本 PR 的浏览器测试只验证路由与缺口状态渲染，不替代双模式 API 安全验收。
