import { Link, NavLink } from 'react-router-dom'

type SidebarProps = {
  collapsed: boolean
  authEnabled: boolean
  version: string
  onToggle: () => void
}

const navigation = [
  { title: 'Agent 协作', icon: '⚇', to: '/workspace/collaboration' },
  { title: 'Workflow', icon: '⌘', to: '/admin/workflows' },
  { title: '成本分摊', icon: '¥', to: '/workspace/quality-cost' },
  { title: '任务中心', icon: '☷', to: '/workspace/tasks' },
  { title: '系统管理', icon: '⚙', to: '/admin/settings' },
  { title: '通知', icon: '♧', to: '/notifications' },
]

export function Sidebar({ collapsed, authEnabled, version, onToggle }: SidebarProps) {
  return (
    <aside className="r3-sidebar" aria-label="主导航">
      <Link className="r3-brand" to="/home">
        <span className="r3-brand-mark">A</span>
        {!collapsed && <span>Agent 平台</span>}
      </Link>
      <button
        className="r3-collapse"
        type="button"
        onClick={onToggle}
        aria-label={collapsed ? '展开导航' : '折叠导航'}
        aria-expanded={!collapsed}
      >
        {collapsed ? '›' : '‹'}
      </button>
      <div className="r3-nav-caption">WORKSPACE</div>
      <nav className="r3-primary-nav" aria-label="工作空间">
        <NavLink to="/home" end className={({ isActive }) => isActive ? 'r3-nav-link active' : 'r3-nav-link'} title={collapsed ? '工作台' : undefined}>
          <span>⌂</span>{!collapsed && '工作台'}
        </NavLink>
        <NavLink to="/office" className={({ isActive }) => isActive ? 'r3-nav-link active' : 'r3-nav-link'} title={collapsed ? '3D 办公室' : undefined}>
          <span>▣</span>{!collapsed && '3D 办公室'}
        </NavLink>
      </nav>
      {!collapsed && <div className="r3-nav-caption r3-nav-caption-secondary">PLATFORM</div>}
      <nav className="r3-primary-nav" aria-label="平台导航">
        {navigation.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => isActive ? 'r3-nav-link active' : 'r3-nav-link'}
            title={collapsed ? item.title : undefined}
          >
            <span>{item.icon}</span>{!collapsed && item.title}
          </NavLink>
        ))}
      </nav>
      {!collapsed && (
        <div className="r3-sidebar-bottom">
          <span className="r3-sidebar-status"><i />{authEnabled ? '权限模式' : '免登录模式'}</span>
          <small>Agent Platform · {version}</small>
        </div>
      )}
    </aside>
  )
}
