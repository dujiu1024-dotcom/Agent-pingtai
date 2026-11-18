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
  return (
    <div className="app-shell">
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
        <div className="sidebar-footer">前端工程基座 · 初始骨架</div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <span className="breadcrumb">平台 / 工作台</span>
          <span className="environment">新前端工程</span>
        </header>
        <section className="welcome">
          <p className="eyebrow">AGENT PLATFORM</p>
          <h1>智能体工作台</h1>
          <p>React 18 · TypeScript · Vite 独立工程已就绪。</p>
        </section>
        <section className="status-grid" aria-label="工程状态">
          <article className="status-card">
            <span className="status-label">前端框架</span>
            <strong>React 18</strong>
            <span className="status-note">独立目录，不覆盖旧 UI</span>
          </article>
          <article className="status-card">
            <span className="status-label">类型系统</span>
            <strong>TypeScript Strict</strong>
            <span className="status-note">严格类型检查已启用</span>
          </article>
          <article className="status-card">
            <span className="status-label">构建工具</span>
            <strong>Vite 6</strong>
            <span className="status-note">生产构建入口已配置</span>
          </article>
        </section>
      </main>
    </div>
  )
}
