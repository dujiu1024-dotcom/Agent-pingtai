import { useEffect, useState } from 'react'
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom'

import { ErrorBoundary } from '@/components/ErrorBoundary'
import { GlobalSearch } from '@/components/GlobalSearch'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  ModeRestrictedState,
  NotFoundState,
} from '@/components/States'
import { NotificationsPage } from '@/components/NotificationsPage'
import { TaskCommandCenter, TaskDetailPage, RunTracePage } from '@/features/tasks/TaskCommandCenter'
import { loadBootstrapConfig } from '@/lib/bootstrap/bootstrap'
import type { BootstrapConfig } from '@/lib/bootstrap/bootstrap'
import {
  authRoutes,
  detailRoutes,
  findPageRoute,
  getVisibleNavigation,
  globalRoutes,
  pageRoutes,
} from '@/routes/route-config'
import type { PageRoute } from '@/routes/route-config'
import { useUiStore } from '@/stores/ui-store'

function BootstrapGate() {
  const [config, setConfig] = useState<BootstrapConfig | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    void loadBootstrapConfig().then(
      (result) => {
        if (active) setConfig(result)
      },
      (reason: unknown) => {
        if (active)
          setError(
            reason instanceof Error ? reason.message : '启动配置读取失败',
          )
      },
    )
    return () => {
      active = false
    }
  }, [attempt])

  if (error) {
    return (
      <div className="bootstrap-screen">
        <ErrorState
          title="无法读取服务端启动配置"
          description={`${error}。为避免猜测 auth.enabled，前端不会自行选择权限模式。`}
          onRetry={() => {
            setError(null)
            setConfig(null)
            setAttempt((value) => value + 1)
          }}
        />
      </div>
    )
  }
  if (!config) {
    return (
      <div className="bootstrap-screen">
        <LoadingState label="正在读取服务端启动配置" />
      </div>
    )
  }

  return <PlatformRouter config={config} />
}

function PlatformRouter({ config }: { config: BootstrapConfig }) {
  const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'
  return (
    <BrowserRouter basename={basename}>
      <ErrorBoundary>
        <Routes>
          <Route element={<AppShell config={config} />}>
            <Route
              path="/"
              element={<Navigate to="/workspace/overview" replace />}
            />
            <Route
              path="/app"
              element={<Navigate to="/workspace/overview" replace />}
            />
            {[...pageRoutes, ...detailRoutes, ...globalRoutes].map((route) => (
              <Route
                key={route.pageId}
                path={route.path}
                element={
                  <PageView
                    route={route}
                    authEnabled={config.auth.enabled}
                    capabilities={config.capabilities}
                  />
                }
              />
            ))}
            {authRoutes.map((route) => (
              <Route
                key={route.pageId}
                path={route.path}
                element={
                  config.auth.enabled ? (
                    <AuthEntryPage route={route} />
                  ) : (
                    <ModeRestrictedState
                      title="当前模式不提供身份入口"
                      description="auth.enabled=false 时不显示登录、注册或管理员初始化流程。"
                    />
                  )
                }
              />
            ))}
            <Route path="*" element={<NotFoundState />} />
          </Route>
        </Routes>
      </ErrorBoundary>
    </BrowserRouter>
  )
}

function AppShell({ config }: { config: BootstrapConfig }) {
  const collapsed = useUiStore((state) => state.sidebarCollapsed)
  const toggleSidebar = useUiStore((state) => state.toggleSidebar)
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        navigate('/search')
      }
    }
    window.addEventListener('keydown', onShortcut)
    return () => window.removeEventListener('keydown', onShortcut)
  }, [navigate])
  const current = [
    ...pageRoutes,
    ...detailRoutes,
    ...globalRoutes,
    ...authRoutes,
  ].find((route) => {
    if (route.path === location.pathname) return true
    const pattern = new RegExp(`^${route.path.replace(/:[^/]+/g, '[^/]+')}/?$`)
    return pattern.test(location.pathname)
  })
  const breadcrumbs = current?.parentPath
    ? [findPageRoute(current.parentPath)].filter((item): item is PageRoute =>
        Boolean(item),
      )
    : []

  return (
    <div className={collapsed ? 'app-shell nav-collapsed' : 'app-shell'}>
      <aside className="sidebar" aria-label="主导航">
        <Link
          className="brand"
          to="/workspace/overview"
          aria-label="Agent Platform 运营驾驶舱"
        >
          <span className="brand-mark">A</span>
          {!collapsed && <span>Agent Platform</span>}
        </Link>
        <button
          className="collapse-button"
          type="button"
          onClick={toggleSidebar}
          aria-label={collapsed ? '展开导航' : '折叠导航'}
          aria-expanded={!collapsed}
        >
          {collapsed ? '›' : '‹'}
        </button>
        <nav className="primary-navigation">
          {getVisibleNavigation(config.auth.enabled, config.capabilities).map(
            (group) => (
              <section
                className="nav-group"
                key={group.id}
                aria-label={group.title}
              >
                <div className="nav-group-title">
                  {!collapsed && group.title}
                </div>
                {group.items.map((item) => (
                  <NavLink
                    key={item.pageId}
                    to={item.path}
                    title={collapsed ? item.title : undefined}
                    className={({ isActive }) =>
                      isActive ? 'nav-item active' : 'nav-item'
                    }
                    end
                  >
                    <span className="nav-dot" aria-hidden="true" />
                    {!collapsed && <span>{item.title}</span>}
                  </NavLink>
                ))}
              </section>
            ),
          )}
        </nav>
        {!collapsed && (
          <div className="sidebar-footer">
            <span>模式由服务端启动配置决定</span>
            <strong>{config.auth.enabled ? '权限模式' : '免登录模式'}</strong>
            <small>版本 {config.version}</small>
          </div>
        )}
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="topbar-start">
            <div className="breadcrumbs" aria-label="面包屑导航">
              <Link to="/workspace/overview">Agent Platform</Link>
              {current && (
                <span>
                  <i aria-hidden="true">/</i>
                  {current.section}
                </span>
              )}
              {breadcrumbs.map((item) => (
                <span key={item.pageId}>
                  <i aria-hidden="true">/</i>
                  {item.title}
                </span>
              ))}
              {current && (
                <span>
                  <i aria-hidden="true">/</i>
                  {current.title}
                </span>
              )}
            </div>
          </div>
          <div className="top-actions">
            <Link className="top-action" to="/search" aria-label="打开全局搜索">
              全局搜索 <kbd>⌘ K</kbd>
            </Link>
            <Link
              className="top-action"
              to="/notifications"
              aria-label="打开通知中心"
            >
              通知
            </Link>
            {config.auth.enabled && (
              <Link className="top-action auth-entry" to="/auth/login">
                登录
              </Link>
            )}
            <span className="mode-indicator">
              {config.auth.enabled ? '权限已启用' : '免登录'}
            </span>
          </div>
        </header>
        <Outlet />
      </main>
    </div>
  )
}

function PageView({
  route,
  authEnabled,
  capabilities,
}: {
  route: PageRoute
  authEnabled: boolean
  capabilities: string[]
}) {
  if (route.authOnly && !authEnabled) {
    return (
      <ModeRestrictedState
        title="此页面仅在权限模式开放"
        description="当前服务端 auth.enabled=false，组织、成员与 RBAC 管理不适用。"
      />
    )
  }
  if (route.capability && !capabilities.includes(route.capability))
    return (
      <ModeRestrictedState
        title="此功能未启用"
        description="服务端 bootstrap 未声明此 capability；前端不自行开启功能。"
      />
    )
  if (route.path === '/workspace/overview') return <Dashboard />
  if (route.path === '/search')
    return (
      <GlobalSearch authEnabled={authEnabled} capabilities={capabilities} />
    )
  if (route.path === '/notifications') return <NotificationsPage />
  if (route.path === '/workspace/tasks') return <TaskCommandCenter />
  if (route.path === '/operations/tasks/:taskId') return <TaskDetailPage />
  if (route.path === '/operations/runs/:runId') return <RunTracePage />
  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">
          {route.pageId} · {route.owner}
        </p>
        <h1>{route.title}</h1>
        <p>{route.description}</p>
      </div>
      <EmptyState
        title="业务页面待模块接入"
        description={`路由与深链已注册。此页面由 ${route.owner} 负责业务实现；本任务不伪造尚未确认的 API、实体数据或操作。`}
      />
    </section>
  )
}

function Dashboard() {
  const shortcuts = [
    {
      title: '任务指挥中心',
      description: '任务队列与执行状态',
      to: '/workspace/tasks',
    },
    {
      title: 'Agent 协作中心',
      description: 'Agent 与 Workflow 运行态',
      to: '/workspace/collaboration',
    },
    {
      title: '成本与质量',
      description: '用量、质量与评测聚合',
      to: '/workspace/quality-cost',
    },
    {
      title: '3D 数字办公室',
      description: '沉浸式办公室入口',
      to: '/workspace/office',
    },
  ]
  return (
    <section className="page-content dashboard-page">
      <div className="dashboard-hero">
        <p className="eyebrow">AGENT PLATFORM / WORKSPACE</p>
        <h1>运营驾驶舱</h1>
        <p>
          统一进入工作空间、智能体运营与平台管理。业务指标将在对应模块 API
          接入后展示。
        </p>
      </div>
      <div className="shortcut-grid">
        {shortcuts.map((item) => (
          <Link className="shortcut-card" to={item.to} key={item.to}>
            <strong>{item.title}</strong>
            <span>{item.description}</span>
            <span className="shortcut-arrow" aria-hidden="true">
              →
            </span>
          </Link>
        ))}
      </div>
      <EmptyState
        title="驾驶舱数据尚未接入"
        description="当前只提供稳定导航和页面框架，不使用静态假指标替代服务端数据。"
      />
    </section>
  )
}

function AuthEntryPage({ route }: { route: PageRoute }) {
  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">{route.pageId}</p>
        <h1>{route.title}</h1>
        <p>{route.description}</p>
      </div>
      <EmptyState
        title="身份流程由认证模块提供"
        description="本任务仅接入服务端权限模式与条件路由，不模拟登录、创建账号或管理员身份。"
        action={
          <Link className="text-link" to="/workspace/overview">
            返回工作空间
          </Link>
        }
      />
    </section>
  )
}

export default function App() {
  return <BootstrapGate />
}
