import './OrganizationAccessPage.css'

type OrganizationAccessPageProps = {
  page: 'organization' | 'rbac'
}

const organizationAreas = [
  {
    title: '组织与部门树',
    description: '组织、部门层级、父子关系和启停生命周期',
    contract: '组织 / 部门 schema 与树查询 API 未核实',
  },
  {
    title: '项目与成员归属',
    description: '项目关联、成员归属、负责人和成员数量',
    contract: '项目成员关系与数据范围 API 未核实',
  },
  {
    title: '成员邀请与生命周期',
    description: '邀请创建、过期、撤销、接受、停用和重新启用',
    contract: '邀请状态机、幂等语义及审计事件未核实',
  },
]

const accessAreas = [
  {
    title: '角色权限矩阵',
    description: '角色与权限集合的绑定、预览和差异比较',
    contract: '权限目录 schema、权限键与服务端 authorizer 未核实',
  },
  {
    title: '资源与操作授权',
    description: '菜单、页面、操作和 API 的服务端授权规则',
    contract: 'resource/action 权限键空间和服务端 authorizer 未核实',
  },
  {
    title: '数据范围策略',
    description: '实例、组织、部门、项目和个人范围的策略配置',
    contract: 'data_scope 枚举、策略继承和过滤语义未定义',
  },
]

const prerequisites = [
  '由服务端 bootstrap 明确返回 auth.enabled 与组织 / RBAC capability。',
  'auth.enabled=false 时隐藏组织、成员和 RBAC 入口，不创建默认组织、用户或角色。',
  'auth.enabled=true 时使用服务端真实 schema、分页 cursor、状态机和错误码。',
  '邀请、停用、角色变更和数据范围变更必须由服务端授权并写入 append-only 审计。',
  '角色变更、成员停用和策略更新必须支持并发冲突检测，不能由前端隐藏按钮代替安全控制。',
]

const notice =
  '当前仓库尚无可证实的组织 / RBAC API 注册与响应 schema。为避免前端猜测字段、授权语义或写入路径，管理操作保持禁用，待后端契约确认后接通。'

export function OrganizationAccessPage({
  page,
}: OrganizationAccessPageProps) {
  const organization = page === 'organization'
  const areas = organization ? organizationAreas : accessAreas

  return (
    <section className="access-admin">
      <div className="access-admin-banner" role="status">
        <span className="access-admin-status">后端契约待接入</span>
        <div>
          <strong>此页面不会展示模拟组织、成员或权限数据</strong>
          <p>{notice}</p>
        </div>
      </div>
      <div className="access-admin-grid">
        {areas.map((area) => (
          <article className="access-admin-card" key={area.title}>
            <div className="access-admin-card-heading">
              <span className="access-admin-icon" aria-hidden="true">
                {organization ? 'ORG' : 'ACL'}
              </span>
              <span className="access-admin-chip">待核实</span>
            </div>
            <h2>{area.title}</h2>
            <p>{area.description}</p>
            <div className="access-admin-contract">
              <span>契约状态</span>
              <strong>{area.contract}</strong>
            </div>
          </article>
        ))}
      </div>
      <section className="access-admin-audit">
        <h2>接入前置条件</h2>
        <ul>
          {prerequisites.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>
    </section>
  )
}
