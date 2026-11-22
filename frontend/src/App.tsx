import { useEffect, useState } from 'react'
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
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
import { PromptRegistryPage } from '@/features/admin/PromptRegistryPage'
import { OfficeWorkspace } from '@/components/OfficeWorkspace'
import { CostQualityCenter } from '@/features/quality-cost/CostQualityCenter'
import { KnowledgeWorkbench } from '@/features/knowledge/KnowledgeWorkbench'
import { FilesWorkspace } from '@/features/files/FilesWorkspace'
import { EvaluationWorkspace } from '@/features/evaluation/EvaluationWorkspace'
import {
  TaskCommandCenter,
  TaskDetailPage,
  RunTracePage,
} from '@/features/tasks/TaskCommandCenter'
import { ModelsManagementPage } from '@/features/admin/ModelsManagementPage'
import { loadBootstrapConfig } from '@/lib/bootstrap/bootstrap'
import type { BootstrapConfig } from '@/lib/bootstrap/bootstrap'
import {
  authRoutes,
  detailRoutes,
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
          <Route element={<AppShell />}>
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
                key={route.path}
                path={route.path}
                element={
                  <RouteGuard
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

function AppShell() {
  const collapsed = useUiStore((state) => state.sidebarCollapsed)
  const toggleSidebar = useUiStore((state) => state.toggleSidebar)
  const navigate = useNavigate()
  const [toast, setToast] = useState('')
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

  const showToast = (message: string) => {
    setToast(message)
    window.setTimeout(() => setToast(''), 1700)
  }

  const navigation = [
    { path: '/workspace/overview', title: '工作台', icon: '⌂', view: 'home' },
    { path: '/workspace/office', title: '3D 办公室', icon: '▣', view: 'office' },
    { path: '/workspace/tasks', title: '任务中心', icon: '☰' },
    { path: '/workspace/collaboration', title: 'Agent 协作', icon: '⚇' },
    { path: '/admin/workflows', title: 'Workflow', icon: '⌘' },
    { path: '/workspace/quality-cost', title: '成本分摊', icon: '¥' },
    { path: '/admin/settings', title: '系统管理', icon: '⚙' },
  ]

  return (
    <div className={collapsed ? 'app-shell nav-collapsed' : 'app-shell'}>
      <aside className="sidebar" aria-label="主导航">
        <Link className="brand" to="/workspace/overview" aria-label="Agent 平台工作台">
          <span className="brand-mark" aria-hidden="true">A</span>
          {!collapsed && <span>Agent 平台</span>}
        </Link>
        <nav className="prototype-navigation" aria-label="全局导航">
          {navigation.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              title={collapsed ? item.title : undefined}
              data-v={item.view}
              end={
                item.path === '/workspace/overview' ||
                item.path === '/workspace/office'
              }
              className={({ isActive }) =>
                isActive ? 'prototype-nav-item on' : 'prototype-nav-item'
              }
            >
              <i aria-hidden="true">{item.icon}</i>
              {!collapsed && <span>{item.title}</span>}
            </NavLink>
          ))}
          <button
            className="prototype-nav-item collapse-nav-item"
            type="button"
            aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
            aria-expanded={!collapsed}
            onClick={() => {
              toggleSidebar()
              showToast(collapsed ? '已展开侧边栏（演示）' : '已收起侧边栏（演示）')
            }}
          >
            <i aria-hidden="true">{collapsed ? '»' : '«'}</i>
            {!collapsed && <span>收起侧边栏</span>}
          </button>
        </nav>
      </aside>

      <main className="main-content">
        <header className="topbar" aria-label="顶部工具栏">
          <form
            className="top-search"
            role="search"
            onSubmit={(event) => {
              event.preventDefault()
              navigate('/search')
            }}
          >
            <span className="top-search-icon" aria-hidden="true">⌕</span>
            <input
              aria-label="搜索 Agent、任务、文档"
              placeholder="搜索 Agent、任务、文档…"
              onKeyDown={(event) => {
                if (event.key === 'Escape') event.currentTarget.blur()
              }}
            />
            <kbd>Ctrl + K</kbd>
          </form>
          <div className="top-actions">
            <Link className="top-icon-button notification-button" to="/notifications" aria-label="通知">
              <span aria-hidden="true">🔔</span>
              <i aria-hidden="true" />
            </Link>
            <button className="top-icon-button" type="button" aria-label="主题" onClick={() => showToast('主题切换待接入')}>
              ☼
            </button>
            <button className="user-avatar" type="button" aria-label="用户账户" onClick={() => showToast('用户菜单待接入')}>
              A
            </button>
          </div>
        </header>
        <Outlet />
      </main>
      <div className={toast ? 'prototype-toast visible' : 'prototype-toast'} role="status" aria-live="polite">
        {toast}
      </div>
    </div>
  )
}

function RouteGuard({
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
  if (route.path === '/admin/prompts' || route.path === '/admin/prompts/:promptId') {
    return (
      <PromptRegistryPage
        evaluationGateEnabled={capabilities.includes('prompt_evaluation_gate')}
      />
    )
  }
  if (route.path === '/workspace/overview') return <Dashboard />
  if (route.path === '/workspace/office') return <OfficeWorkspace />
  if (route.path === '/search')
    return (
      <GlobalSearch authEnabled={authEnabled} capabilities={capabilities} />
    )
  if (route.path === '/notifications') return <NotificationsPage />
  if (route.path === '/workspace/tasks') return <TaskCommandCenter />
  if (route.path === '/workspace/quality-cost') return <CostQualityCenter />
  if (route.path === '/operations/knowledge-bases' || route.path === '/operations/knowledge-bases/:collectionId' || route.path === '/operations/documents/:documentId') return <KnowledgeWorkbench />
  if (route.path === '/operations/files') return <FilesWorkspace authEnabled={authEnabled} />
  if (['/operations/evaluation/datasets', '/operations/evaluation/datasets/:datasetId', '/operations/evaluation/runs', '/operations/evaluation/runs/:evaluationRunId'].includes(route.path)) return <EvaluationWorkspace />
  if (route.path === '/operations/tasks/:taskId') return <TaskDetailPage />
  if (route.path === '/operations/runs/:runId') return <RunTracePage />
  if (route.path.startsWith('/admin/models/routing')) return <ModelsManagementPage authEnabled={authEnabled} />
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
