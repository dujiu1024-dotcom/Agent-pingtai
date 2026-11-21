import { Suspense, lazy, useEffect, useState } from 'react'
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
  Settings,
  Users,
  Wallet,
  Workflow,
} from 'lucide-react'

import { ErrorBoundary } from '@/components/ErrorBoundary'
import avatarImage from '@/assets/avatar.png'
import { GlobalSearch } from '@/components/GlobalSearch'
import { InitializePage, LoginPage } from '@/components/AuthPages'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  ModeRestrictedState,
  NotFoundState,
} from '@/components/States'
import { AuthProvider, useAuth } from '@/providers/AuthProvider'
import { GenericListPage } from '@/components/GenericListPage'
import { CrudListPage } from '@/components/crud/CrudListPage'
import { RegistryCatalogPage } from '@/components/crud/RegistryCatalogPage'
import { registryConfigs } from '@/components/crud/registry-configs'
import {
  approvalConfig,
  conversationConfig,
  evaluationDatasetConfig,
  evaluationRunConfig,
  fileConfig,
  schedulerJobConfig,
  subscriptionConfig,
  connectorConfig,
  webhookConfig,
} from '@/components/crud/resource-configs'
import type { CrudConfig } from '@/components/crud/types'
import { loadBootstrapConfig } from '@/lib/bootstrap/bootstrap'
import type { BootstrapConfig } from '@/lib/bootstrap/bootstrap'
import {
  detailRoutes,
  findNavigationSection,
  findSubPage,
  getNavigationSections,
  globalRoutes,
  pageRoutes,
} from '@/routes/route-config'
import type { PageRoute } from '@/routes/route-config'
import { useUiStore } from '@/stores/ui-store'

// 路由级代码分块：每个 feature 页面独立 chunk
const Dashboard = lazy(() => import('@/components/OperationsDashboard').then((m) => ({ default: m.Dashboard })))
const QuickStartPage = lazy(() => import('@/features/quickstart/QuickStartPage').then((m) => ({ default: m.QuickStartPage })))
const StatusBoardPage = lazy(() => import('@/features/board/StatusBoardPage').then((m) => ({ default: m.StatusBoardPage })))
const NotificationsPage = lazy(() => import('@/components/NotificationsPage').then((m) => ({ default: m.NotificationsPage })))
const OfficeWorkspace = lazy(() => import('@/components/OfficeWorkspace').then((m) => ({ default: m.OfficeWorkspace })))
const OrganizationAccessPage = lazy(() => import('@/components/OrganizationAccessPage').then((m) => ({ default: m.OrganizationAccessPage })))
const SystemSettingsPage = lazy(() => import('@/components/SystemSettingsPage').then((m) => ({ default: m.SystemSettingsPage })))
const DetailRoutePage = lazy(() => import('@/components/EntityDetailPage').then((m) => ({ default: m.DetailRoutePage })))

const TaskCommandCenter = lazy(() => import('@/features/tasks/TaskCommandCenter').then((m) => ({ default: m.TaskCommandCenter })))
const TaskDetailPage = lazy(() => import('@/features/tasks/TaskCommandCenter').then((m) => ({ default: m.TaskDetailPage })))
const RunTracePage = lazy(() => import('@/features/tasks/TaskCommandCenter').then((m) => ({ default: m.RunTracePage })))
const CostQualityCenter = lazy(() => import('@/features/quality-cost/CostQualityCenter').then((m) => ({ default: m.CostQualityCenter })))
const KnowledgeWorkbench = lazy(() => import('@/features/knowledge/KnowledgeWorkbench').then((m) => ({ default: m.KnowledgeWorkbench })))
const KnowledgeGraphPage = lazy(() => import('@/features/knowledge/KnowledgeGraphPage').then((m) => ({ default: m.KnowledgeGraphPage })))
const AgentCollaborationCenter = lazy(() => import('@/features/collaboration/AgentCollaborationCenter').then((m) => ({ default: m.AgentCollaborationCenter })))
const AgentDefinitionPage = lazy(() => import('@/features/agents/AgentDefinitionPage').then((m) => ({ default: m.AgentDefinitionPage })))
const WorkflowDesignerPage = lazy(() => import('@/features/workflows/WorkflowDesignerPage').then((m) => ({ default: m.WorkflowDesignerPage })))
const AlertsPage = lazy(() => import('@/features/alerts/AlertsPage').then((m) => ({ default: m.AlertsPage })))
const BatchExecutionPage = lazy(() => import('@/features/batches/BatchExecutionPage').then((m) => ({ default: m.BatchExecutionPage })))
const RunReplayPage = lazy(() => import('@/features/replay/RunReplayPage').then((m) => ({ default: m.RunReplayPage })))
const RagPlaygroundPage = lazy(() => import('@/features/rag/RagPlaygroundPage').then((m) => ({ default: m.RagPlaygroundPage })))
const ExtensionPublishersPage = lazy(() => import('@/features/extensions/ExtensionPublishersPage').then((m) => ({ default: m.ExtensionPublishersPage })))
const PromptLifecyclePage = lazy(() => import('@/features/prompts/PromptLifecyclePage').then((m) => ({ default: m.PromptLifecyclePage })))
const AuditCenterPage = lazy(() => import('@/features/audit/AuditCenterPage').then((m) => ({ default: m.AuditCenterPage })))
const AuditEventDetailPage = lazy(() => import('@/features/audit/AuditEventDetailPage').then((m) => ({ default: m.AuditEventDetailPage })))
const ApprovalDetailPage = lazy(() => import('@/features/approvals/ApprovalDetailPage').then((m) => ({ default: m.ApprovalDetailPage })))
const MemoryGovernancePage = lazy(() => import('@/features/memories/MemoryGovernancePage').then((m) => ({ default: m.MemoryGovernancePage })))
const EvaluationInsightsPage = lazy(() => import('@/features/evaluation/EvaluationInsightsPage').then((m) => ({ default: m.EvaluationInsightsPage })))

const PageFallback = () => <LoadingState label="正在加载页面" />

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

  return (
    <AuthProvider enabled={config.auth.enabled}>
      <PlatformRouter config={config} />
    </AuthProvider>
  )
}

function RequireSession({
  enabled,
  children,
}: {
  enabled: boolean
  children: React.ReactNode
}) {
  const auth = useAuth()
  const location = useLocation()
  if (!enabled) return <>{children}</>
  if (auth.status === 'loading') {
    return (
      <div className="bootstrap-screen">
        <LoadingState label="正在校验登录状态" />
      </div>
    )
  }
  if (auth.status === 'anonymous') {
    return (
      <Navigate
        to="/auth/login"
        replace
        state={{ from: location.pathname }}
      />
    )
  }
  return <>{children}</>
}

function PlatformRouter({ config }: { config: BootstrapConfig }) {
  const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'
  return (
    <BrowserRouter basename={basename}>
      <ErrorBoundary>
        <Routes>
          <Route path="/auth/login" element={<LoginPage />} />
          <Route path="/auth/initialize" element={<InitializePage />} />
          <Route
            element={
              <RequireSession enabled={config.auth.enabled}>
                <AppShell config={config} />
              </RequireSession>
            }
          >
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
                  />
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
  const auth = useAuth()
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
    const vars = [
      '--ui-scale',
      '--ui-canvas-width',
      '--ui-canvas-height',
      '--ui-dx',
      '--ui-dy',
      '--ui-side',
      '--ui-offset',
    ]
    const clear = () => {
      root.classList.remove('scaled-ui')
      vars.forEach((name) => root.style.removeProperty(name))
    }
    const apply = () => {
      if (window.innerWidth < 1181) {
        clear()
        return
      }
      const width = window.innerWidth
      const height = window.innerHeight
      let scale = height / 1024
      let canvasWidth = Math.min(width / scale, 2600)
      if (canvasWidth < 1536) {
        scale = width / 1536
        canvasWidth = 1536
      }
      const canvasHeight = Math.max(1024, height / scale)
      root.style.setProperty('--ui-scale', scale.toFixed(4))
      root.style.setProperty('--ui-canvas-width', `${canvasWidth.toFixed(2)}px`)
      root.style.setProperty(
        '--ui-canvas-height',
        `${canvasHeight.toFixed(2)}px`,
      )
      root.style.setProperty(
        '--ui-dx',
        `${(canvasWidth - 1536).toFixed(2)}px`,
      )
      root.style.setProperty(
        '--ui-dy',
        `${(canvasHeight - 1024).toFixed(2)}px`,
      )
      root.style.setProperty('--ui-side', '0px')
      root.style.setProperty('--ui-offset', '0px')
      root.classList.add('scaled-ui')
    }
    apply()
    window.addEventListener('resize', apply)
    return () => {
      window.removeEventListener('resize', apply)
      clear()
    }
  }, [])
  const current = [
    ...pageRoutes,
    ...detailRoutes,
    ...globalRoutes,
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
    administration: Settings,
  }

  return (
    <div className={collapsed ? 'app-shell nav-collapsed' : 'app-shell'}>
      <aside className="sidebar" aria-label="主导航">
        <Link
          className="brand"
          to="/workspace/overview"
          aria-label="龍奕 FDE 工作室工作台"
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
          {!collapsed && <span className="brand-name">龍奕 FDE 工作室</span>}
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
            {config.auth.enabled && auth.session?.user && (
              <span className="top-action auth-entry">
                <LogIn size={14} strokeWidth={2} aria-hidden="true" />
                {auth.session.user.display_name || auth.session.user.email}
                <button
                  className="text-link"
                  type="button"
                  onClick={() => void auth.signOut()}
                >
                  退出
                </button>
              </span>
            )}
            <img
              className="avatar"
              src={avatarImage}
              alt=""
              aria-hidden="true"
            />
          </div>
        </header>
        <nav className="page-breadcrumb" aria-label="面包屑">
          <Link to="/workspace/overview">首页</Link>
          {section && (
            <>
              <span className="breadcrumb-sep">/</span>
              <Link to={section.path}>{section.label}</Link>
            </>
          )}
          {activePage && (
            <>
              <span className="breadcrumb-sep">/</span>
              {current && current.path !== activePage.path ? (
                <Link to={activePage.path}>{activePage.label}</Link>
              ) : (
                <span aria-current="page">{activePage.label}</span>
              )}
            </>
          )}
          {current && activePage && current.path !== activePage.path && (
            <>
              <span className="breadcrumb-sep">/</span>
              <span aria-current="page">{current.title}</span>
            </>
          )}
        </nav>
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

const CRUD_CONFIGS: Record<string, CrudConfig> = {
  '/operations/approvals': approvalConfig,
  '/operations/conversations': conversationConfig,
  '/operations/files': fileConfig,
  '/operations/evaluation/datasets': evaluationDatasetConfig,
  '/operations/evaluation/runs': evaluationRunConfig,
  '/admin/scheduler/jobs': schedulerJobConfig,
  '/admin/scheduler/subscriptions': subscriptionConfig,
  '/admin/connectors': connectorConfig,
  '/admin/webhooks': webhookConfig,
}

function resolveApiPath(routePath: string): string | null {
  /** 前端路由 → 后端 API 路径映射；返回 null 表示此路由无 API 可接 */
  const map: Record<string, string> = {
    // 审计
    '/admin/audit': '/platform/v1/audit/identity-events',
    // Organization / RBAC / Settings（admin.py 新增，仅只读）
    '/admin/organization': '/platform/v1/admin/organization',
    '/admin/access-control': '/platform/v1/admin/access-control',
    // Metrics JSON
    '/admin/operations': '/platform/v1/observability/metrics/json',
  }
  return map[routePath] ?? null
}

function Lazy({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<PageFallback />}>{children}</Suspense>
}

function PageView({
  route,
  authEnabled,
}: {
  route: PageRoute
  authEnabled: boolean
}) {
  if (route.authOnly && !authEnabled) {
    return (
      <ModeRestrictedState
        title="此页面仅在权限模式开放"
        description="当前服务端 auth.enabled=false，组织、成员与 RBAC 管理不适用。"
      />
    )
  }
  if (route.path === '/workspace/overview') return <Lazy><Dashboard /></Lazy>
  if (route.path === '/workspace/quickstart') return <Lazy><QuickStartPage /></Lazy>
  if (route.path === '/workspace/status') return <Lazy><StatusBoardPage /></Lazy>
  if (route.path === '/search') return <GlobalSearch />
  if (route.path === '/notifications') return <Lazy><NotificationsPage /></Lazy>
  if (route.path === '/workspace/tasks') return <Lazy><TaskCommandCenter /></Lazy>
  if (
    route.path === '/workspace/collaboration' ||
    route.path === '/operations/blackboard/:entryId'
  )
    return <Lazy><AgentCollaborationCenter /></Lazy>
  if (route.path === '/workspace/quality-cost') return <Lazy><CostQualityCenter /></Lazy>
  if (route.path === '/workspace/office') return <Lazy><OfficeWorkspace /></Lazy>
  if (route.path === '/admin/organization')
    return <Lazy><OrganizationAccessPage page="organization" /></Lazy>
  if (route.path === '/admin/access-control')
    return <Lazy><OrganizationAccessPage page="rbac" /></Lazy>
  if (route.path === '/admin/rbac')
    return <Navigate to="/admin/access-control" replace />
  if (route.path === '/admin/settings') return <Lazy><SystemSettingsPage /></Lazy>
  if (route.path === '/operations/approvals/:approvalId')
    return <Lazy><ApprovalDetailPage /></Lazy>
  if (route.path === '/admin/audit/:eventId') return <Lazy><AuditEventDetailPage /></Lazy>
  if (
    route.path === '/operations/agents' ||
    route.path === '/operations/agents/:agentId' ||
    route.path === '/admin/agents' ||
    route.path === '/admin/agents/:agentId'
  )
    return <Lazy><AgentDefinitionPage /></Lazy>
  if (
    route.path === '/admin/workflows' ||
    route.path === '/admin/workflows/:workflowId' ||
    route.path === '/admin/workflows/:workflowId/edit'
  )
    return <Lazy><WorkflowDesignerPage /></Lazy>
  if (
    route.path === '/operations/knowledge-bases' ||
    route.path === '/operations/knowledge-bases/:collectionId' ||
    route.path === '/operations/documents/:documentId'
  )
    return <Lazy><KnowledgeWorkbench /></Lazy>
  if (route.path === '/operations/knowledge-graph') return <Lazy><KnowledgeGraphPage /></Lazy>
  if (route.path === '/operations/rag-playground') return <Lazy><RagPlaygroundPage /></Lazy>
  if (route.path === '/operations/batches') return <Lazy><BatchExecutionPage /></Lazy>
  if (route.path === '/operations/replay') return <Lazy><RunReplayPage /></Lazy>
  if (route.path === '/operations/alerts') return <Lazy><AlertsPage /></Lazy>
  if (route.path === '/admin/extensions') return <Lazy><ExtensionPublishersPage /></Lazy>
  if (route.path === '/admin/prompts') return <Lazy><PromptLifecyclePage /></Lazy>
  if (route.path === '/admin/audit') return <Lazy><AuditCenterPage /></Lazy>
  if (route.path === '/operations/memories') return <Lazy><MemoryGovernancePage /></Lazy>
  if (route.path === '/operations/evaluation/insights') return <Lazy><EvaluationInsightsPage /></Lazy>
  if (route.path === '/operations/tasks/:taskId') return <Lazy><TaskDetailPage /></Lazy>
  if (route.path === '/operations/runs/:runId') return <Lazy><RunTracePage /></Lazy>

  // 资源列表 → 通用 CRUD（列表内弹窗新建/编辑）
  const crudConfig = CRUD_CONFIGS[route.path]
  if (crudConfig) return <CrudListPage config={crudConfig} />
  const registryConfig = registryConfigs[route.path]
  if (registryConfig) return <RegistryCatalogPage config={registryConfig} />

  // 通用分支：后端有 API → GenericListPage
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

  // 其余详情路由 → 通用详情页（逐个映射到真实 GET 接口）
  if (route.path.includes('/:')) return <Lazy><DetailRoutePage path={route.path} /></Lazy>

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

export default function App() {
  return <BootstrapGate />
}
