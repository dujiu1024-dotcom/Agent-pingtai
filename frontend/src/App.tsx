// @ts-nocheck
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
import {
  Bell,
  Building2,
  ChevronsLeft,
  ChevronsRight,
  Home,
  ListTodo,
  LogIn,
  Search,
  UserRound,
  Users,
  Wallet,
  Workflow,
} from 'lucide-react'

import { ErrorBoundary } from '@/components/ErrorBoundary'
import { GlobalSearch } from '@/components/GlobalSearch'
import { PromptRegistryPage } from '@/features/admin/PromptRegistryPage'
import { ModelsManagementPage } from '@/features/admin/ModelsManagementPage'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  ModeRestrictedState,
  NotFoundState,
} from '@/components/States'
import { NotificationsPage } from '@/components/NotificationsPage'
import { CostQualityCenter } from '@/features/quality-cost/CostQualityCenter'
import { KnowledgeWorkbench } from '@/features/knowledge/KnowledgeWorkbench'
import {
  TaskCommandCenter,
  TaskDetailPage,
  RunTracePage,
} from '@/features/tasks/TaskCommandCenter'
import { AgentCollaborationCenter } from '@/features/collaboration/AgentCollaborationCenter'
import { AgentDefinitionPage } from '@/features/agents/AgentDefinitionPage'
import { WorkflowDesignerPage } from '@/features/workflows/WorkflowDesignerPage'
import { OfficeWorkspace } from '@/components/OfficeWorkspace'
import { GenericListPage } from '@/components/GenericListPage'
import { OrganizationAccessPage } from '@/components/OrganizationAccessPage'
import { Dashboard } from '@/components/OperationsDashboard'
import { QuickStartPage } from '@/features/quickstart/QuickStartPage'
import { StatusBoardPage } from '@/features/board/StatusBoardPage'
import { loadBootstrapConfig } from '@/lib/bootstrap/bootstrap'
import type { BootstrapConfig } from '@/lib/bootstrap/bootstrap'
import {
  authRoutes,
  detailRoutes,
  findNavigationSection,
  findSubPage,
  getNavigationSections,
  getWorkspaceViewMode,
  globalRoutes,
  pageRoutes,
} from '@/routes/route-config'
import type { PageRoute } from '@/routes/route-config'
import { useUiStore } from '@/stores/ui-store'
import { AdminInitPage, LoginPage } from '@/features/auth/AuthPages'

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
                    <Navigate to="/workspace/overview" replace />
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
  useEffect(() => {
    const root = document.documentElement
    const apply = () => {
      const raw = Math.min(window.innerWidth / 1536, window.innerHeight / 1024)
      const scale = Number(Math.min(1.5, Math.max(0.7, raw)).toFixed(4))
      if (window.innerWidth >= 1181) {
        const offset = Math.max(0, (window.innerWidth / scale - 1536) / 2)
        const side = Math.max(0, (window.innerWidth - 1536 * scale) / 2)
        root.style.setProperty('--ui-scale', String(scale))
        root.style.setProperty('--ui-offset', `${offset.toFixed(2)}px`)
        root.style.setProperty('--ui-side', `${side.toFixed(2)}px`)
        root.classList.add('scaled-ui')
      } else {
        root.classList.remove('scaled-ui')
        root.style.removeProperty('--ui-scale')
        root.style.removeProperty('--ui-offset')
        root.style.removeProperty('--ui-side')
      }
    }
    apply()
    window.addEventListener('resize', apply)
    return () => {
      window.removeEventListener('resize', apply)
      root.classList.remove('scaled-ui')
      root.style.removeProperty('--ui-scale')
      root.style.removeProperty('--ui-offset')
      root.style.removeProperty('--ui-side')
    }
  }, [])
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
  const sections = getNavigationSections(
    config.auth.enabled,
    config.capabilities,
  )
  const effectivePath = current?.parentPath ?? location.pathname
  const section = findNavigationSection(effectivePath)
  const activePage = findSubPage(section, effectivePath)
  const navIcons: Record<string, typeof Home> = {
    home: Home,
    office: Building2,
    tasks: ListTodo,
    collaboration: Users,
    workflow: Workflow,
    cost: Wallet,
  }

  return (
    <div
      className={collapsed ? 'app-shell nav-collapsed' : 'app-shell'}
      data-v={getWorkspaceViewMode(location.pathname)}
    >
      <aside className="sidebar" aria-label="主导航">
        <Link
          className="brand"
          to="/workspace/overview"
          aria-label="Agent 平台工作台"
        >
          <span className="brand-mark" aria-hidden="true">
            <svg width="24" height="24" viewBox="0 0 26 26">
              <defs>
                <linearGradient id="brand-gradient" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stopColor="#6aa4ff" />
                  <stop offset="1" stopColor="#6d4af0" />
                </linearGradient>
              </defs>
              <path
                d="M13 1 25 24h-6l-6-12-6 12H1z"
                fill="url(#brand-gradient)"
              />
            </svg>
          </span>
          {!collapsed && <span className="brand-name">Agent 平台</span>}
        </Link>
        <nav className="primary-navigation">
          {sections.map((item) => {
            const Icon = navIcons[item.id] ?? Home
            return (
              <Link
                key={item.id}
                to={item.path}
                title={collapsed ? item.label : undefined}
                className={
                  section?.id === item.id ? 'nav-item active' : 'nav-item'
                }
                aria-current={section?.id === item.id ? 'page' : undefined}
              >
                <span className="nav-icon" aria-hidden="true">
                  <Icon size={18} strokeWidth={1.9} />
                </span>
                {!collapsed && <span>{item.label}</span>}
              </Link>
            )
          })}
        </nav>
        <button
          className="collapse-button"
          type="button"
          onClick={toggleSidebar}
          aria-label={collapsed ? '展开导航' : '折叠导航'}
          aria-expanded={!collapsed}
        >
          <span className="nav-icon" aria-hidden="true">
            {collapsed ? (
              <ChevronsRight size={17} />
            ) : (
              <ChevronsLeft size={17} />
            )}
          </span>
          {!collapsed && <span>收起侧边栏</span>}
        </button>
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
          <Link
            className="global-search"
            to="/search"
            aria-label="打开全局搜索"
          >
            <Search size={16} strokeWidth={2} aria-hidden="true" />
            <span className="global-search-placeholder">
              搜索 Agent、任务、文档…
            </span>
            <kbd>Ctrl K</kbd>
          </Link>
          <div className="top-actions">
            <Link
              className="icon-button"
              to="/notifications"
              aria-label="打开通知中心"
            >
              <Bell size={18} strokeWidth={1.9} />
            </Link>
            <span className="mode-indicator">
              {config.auth.enabled ? '权限已启用' : '免登录'}
            </span>
            {config.auth.enabled && (
              <Link className="top-action auth-entry" to="/auth/login">
                <LogIn size={14} strokeWidth={2} aria-hidden="true" />
                登录
              </Link>
            )}
            <span className="avatar" aria-hidden="true">
              <UserRound size={20} strokeWidth={1.8} />
            </span>
          </div>
        </header>
        {collapsed && (
          <div className="sidebar-collapsed-hint" role="status">
            已收起侧边栏（演示）
          </div>
        )}
        {section && section.subpages.length > 1 && (
          <nav className="subnav" aria-label={`${section.label}子页面`}>
            {section.subpages.map((page) => (
              <NavLink
                key={page.path}
                to={page.path}
                end
                className={
                  activePage?.path === page.path
                    ? 'subnav-item active'
                    : 'subnav-item'
                }
              >
                {page.label}
              </NavLink>
            ))}
          </nav>
        )}
        <Outlet />
      </main>
    </div>
  )
}

function resolveApiPath(routePath: string): string | null {
  /** 前端路由 → 后端 API 路径映射；返回 null 表示此路由无 API 可接 */
  const map: Record<string, string> = {
    // 运营
    '/operations/approvals': '/platform/v1/approvals',
    '/operations/memories': '/platform/v1/memories',
    '/operations/conversations': '/platform/v1/sessions',
    '/operations/files': '/platform/v1/files',
    '/admin/audit': '/platform/v1/audit/identity-events',
    // Registry 类 — 统一走 /platform/v1/registry/{kind}
    '/admin/models/providers': '/platform/v1/registry/providers',
    '/admin/models/templates': '/platform/v1/registry/models',
    '/admin/models/routing': '/platform/v1/registry/routing-policies',
    '/admin/tools': '/platform/v1/registry/tools',
    '/admin/skills': '/platform/v1/registry/skills',
    // 调度
    '/admin/scheduler/jobs': '/platform/v1/scheduler/jobs',
    '/admin/scheduler/subscriptions': '/platform/v1/scheduler/subscriptions',
    // 评测（后端已补 list）
    '/operations/evaluation/datasets':
      '/platform/v1/observability/evaluations/datasets',
    '/operations/evaluation/runs': '/platform/v1/observability/evaluations',
    // Prompt（后端已补 list）
    '/admin/prompts': '/platform/v1/prompts',
    // Organization / RBAC / Settings（admin.py 新增）
    '/admin/organization': '/platform/v1/admin/organization',
    '/admin/access-control': '/platform/v1/admin/access-control',
    '/admin/settings': '/platform/v1/admin/settings',
    // Metrics JSON
    '/admin/operations': '/platform/v1/observability/metrics/json',
  }
  if (map[routePath]) return map[routePath]
  // 详情路由（含 /:param）一律返回 null — 不降级为父级 list API
  // 详情数据需要专门组件 + 单条 GET {id} 接口
  if (routePath.includes('/:')) return null
  // Auth 相关 — 不渲染的空壳
  if (routePath.startsWith('/auth/')) return null
  // Organization / RBAC / Settings — 后端无 API
  if (
    [
      '/admin/organization',
      '/admin/access-control',
      '/admin/settings',
    ].includes(routePath)
  )
    return null
  return null
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
  if (route.capability && !capabilities.includes(route.capability)) {
    return (
      <ModeRestrictedState
        title="当前实例未启用此能力"
        description={`服务端启动配置未声明 ${route.capability}，此入口不会尝试调用对应 API。`}
      />
    )
  }
  if (route.authOnly && !authEnabled) {
    return (
      <ModeRestrictedState
        title="此页面仅在权限模式开放"
        description="当前服务端 auth.enabled=false，组织、成员与 RBAC 管理不适用。"
      />
    )
  }
  if (route.capability && !capabilities.includes(route.capability)) {
    return (
      <ModeRestrictedState
        title="当前实例未启用此能力"
        description="服务端 bootstrap 未声明所需 capability，页面不会尝试调用未配置的管理 API。"
      />
    )
  }
  if (
    route.path === '/admin/prompts' ||
    route.path === '/admin/prompts/:promptId'
  ) {
    return (
      <PromptRegistryPage
        evaluationGateEnabled={capabilities.includes('prompt_evaluation_gate')}
      />
    )
  }
  if (route.path === '/admin/access-control') {
    return <Navigate to="/admin/rbac" replace />
  }
  if (route.path === '/admin/organization') {
    return <OrganizationAccessPage page="organization" />
  }
  if (route.path === '/admin/rbac') {
    return <OrganizationAccessPage page="rbac" />
  }
  if (route.path === '/workspace/overview') return <Dashboard />
  if (route.path === '/workspace/quickstart') return <QuickStartPage />
  if (route.path === '/workspace/status') return <StatusBoardPage />
  if (route.path === '/search')
    return (
      <GlobalSearch authEnabled={authEnabled} capabilities={capabilities} />
    )
  if (route.path === '/notifications') return <NotificationsPage />
  if (route.path === '/workspace/tasks') return <TaskCommandCenter />
  if (
    route.path === '/workspace/collaboration' ||
    route.path === '/operations/blackboard/:entryId'
  )
    return <AgentCollaborationCenter />
  if (route.path === '/workspace/quality-cost') return <CostQualityCenter />
  if (route.path === '/workspace/office') return <OfficeWorkspace />
  if (
    route.path === '/operations/agents' ||
    route.path === '/operations/agents/:agentId' ||
    route.path === '/admin/agents' ||
    route.path === '/admin/agents/:agentId'
  )
    return <AgentDefinitionPage />
  if (
    route.path === '/admin/workflows' ||
    route.path === '/admin/workflows/:workflowId' ||
    route.path === '/admin/workflows/:workflowId/edit' ||
    route.path === '/operations/workflow-runs/:runId'
  )
    return <WorkflowDesignerPage />
  if (
    route.path === '/operations/knowledge-bases' ||
    route.path === '/operations/knowledge-bases/:collectionId' ||
    route.path === '/operations/documents/:documentId'
  )
    return <KnowledgeWorkbench />
  if (route.path === '/operations/tasks/:taskId') return <TaskDetailPage />
  if (route.path === '/operations/runs/:runId') return <RunTracePage />
  if (route.path.startsWith('/admin/models/')) return <ModelsManagementPage />

  // 通用分支：后端有 API → GenericListPage，无 API → EmptyState
  const apiPath = resolveApiPath(route.path)
  if (apiPath) {
    return (
      <GenericListPage
        title={route.title}
        description={route.description}
        apiPath={apiPath}
        queryKey={route.path}
      />
    )
  }

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
        description={`路由已注册，但服务端未声明可调用的 API 路径。由 ${route.owner} 负责业务实现。`}
      />
    </section>
  )
}

function AuthEntryPage({ route }: { route: PageRoute }) {
  if (route.path === '/auth/login') return <LoginPage />
  if (route.path === '/auth/initialize') return <AdminInitPage />
  return <NotFoundState />
}

export default function App() {
  return <BootstrapGate />
}
