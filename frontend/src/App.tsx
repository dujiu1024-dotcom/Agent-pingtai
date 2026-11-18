import DesignSystemShowcase from '@/components/DesignSystemShowcase'
import { useUiStore } from '@/stores/ui-store'

const navigation = [
  { label: '工作台', active: true },
  { label: '智能体', active: false },
  { label: '工作流', active: false },
  { label: '任务中心', active: false },
  { label: '模型配置', active: false },
  { label: '知识库', active: false },
  { label: '系统设置', active: false },
]

export default function App() {
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed)
  const toggleSidebar = useUiStore((state) => state.toggleSidebar)

  return (
    <div className={sidebarCollapsed ? 'app-shell sidebar-collapsed' : 'app-shell'}>
      <aside className="sidebar" aria-label="主导航">
        <a className="brand" href="/" aria-label="Agent Platform 首页">
          <span className="brand-mark">A</span>
          <span>Agent Platform</span>
        </a>
        <div className="nav-caption">平台</div>
        <nav>
          {navigation.map((item) => (
            <a
              className={item.active ? 'nav-item active' : 'nav-item'}
              href="#"
              aria-current={item.active ? 'page' : undefined}
              key={item.label}
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="sidebar-footer">前端工程基座 · 设计系统样例</div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div className="topbar-start">
            <button
              className="sidebar-toggle"
              type="button"
              onClick={toggleSidebar}
              aria-label={sidebarCollapsed ? '展开导航' : '收起导航'}
              aria-expanded={!sidebarCollapsed}
            >
              {sidebarCollapsed ? '展开导航' : '收起导航'}
            </button>
            <span className="breadcrumb">平台 / 工作台</span>
          </div>
          <span className="environment">新前端工程</span>
        </header>
        <section className="welcome">
          <p className="eyebrow">AGENT PLATFORM</p>
          <h1>智能体工作台</h1>
          <p>企业级智能体平台 · 统一设计语言</p>
        </section>
        <DesignSystemShowcase />
      </main>
    </div>
  )
}
