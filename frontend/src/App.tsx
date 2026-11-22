import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useNavigate,
  useLocation,
  useParams,
} from 'react-router-dom'

import { ErrorBoundary } from '@/components/ErrorBoundary'
import DesignSystemShowcase from '@/components/DesignSystemShowcase'
import { GlobalSearch } from '@/components/GlobalSearch'
import { SystemSettingsPage } from '@/features/admin/SystemSettingsPage'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  ModeRestrictedState,
  NotFoundState,
  ForbiddenState,
} from '@/components/States'
import { NotificationsPage } from '@/components/NotificationsPage'
import { OrganizationAccessPage } from '@/components/OrganizationAccessPage'
import { PromptRegistryPage } from '@/features/admin/PromptRegistryPage'
import { Dashboard } from '@/features/dashboard/Dashboard'
import { OperationsDashboard } from '@/components'
import { HomeWorkspace } from '@/features/home/HomeWorkspace'
import { OfficeWorkspace } from '@/components/OfficeWorkspace'
import { CostQualityCenter } from '@/features/quality-cost/CostQualityCenter'
import { SystemOpsPage } from '@/features/admin/SystemOpsPage'
import { AgentRuntimeList } from '@/features/agents/AgentRuntimePage'
import { KnowledgeWorkbench } from '@/features/knowledge/KnowledgeWorkbench'
import { SessionFileWorkbench } from '@/features/sessions/SessionFileWorkbench'
import { EvaluationWorkspace } from '@/features/evaluation/EvaluationWorkspace'
import { ApprovalDetailPage } from '@/features/approvals/ApprovalDetailPage'
import { GenericListPage } from '@/components/GenericListPage'
import { ToolsRegistryPage } from '@/features/tools/ToolsRegistryPage'
import { SkillsPage } from '@/features/admin/SkillsPage'
import { ToolSkillResourcePage } from '@/features/resources/ToolSkillResourcePage'
import {
  TaskCommandCenter,
  TaskDetailPage,
  RunTracePage,
} from '@/features/tasks/TaskCommandCenter'
import { TaskRunTimeline } from '@/features/tasks/TaskRunTimeline'
import { ModelsManagementPage } from '@/features/admin/ModelsManagementPage'
import { AgentDefinitionPage } from '@/features/agents/AgentDefinitionPage'
import { WorkflowDesignerPage } from '@/features/workflows/WorkflowDesignerPage'
import { AgentCollaborationCenter } from '@/features/collaboration/AgentCollaborationCenter'
import { loadBootstrapConfig } from '@/lib/bootstrap/bootstrap'
import type { BootstrapConfig } from '@/lib/bootstrap/bootstrap'
import {
  authRoutes,
  detailRoutes,
  globalRoutes,
  pageRoutes,
  navigationSections,
  getRouteBreadcrumbs,
  getAuthEntryRedirect,
} from '@/routes/route-config'
import type { PageRoute } from '@/routes/route-config'
import { useUiStore } from '@/stores/ui-store'
import { AdminInitPage, LoginPage } from '@/features/auth/AuthPages'
import '@/routes/issue-818-breadcrumbs.css'

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
              path="/_storybook/design-system"
              element={<DesignSystemShowcase />}
            />
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
                  route.path === '/403' ? (
                    <ForbiddenState />
                  ) : route.path === '/404' ? (
                    <NotFoundState />
                  ) : (
                  <RouteGuard
                    route={route}
                    authEnabled={config.auth.enabled}
                    capabilities={config.capabilities}
                    version={config.version}
                  />
                  )
                }
              />
            ))}
            {authRoutes.map((route) => (
              <Route
                key={route.pageId}
                path={route.path}
                element={
                  getAuthEntryRedirect(config.auth.enabled) ? (
                    <Navigate to={getAuthEntryRedirect(config.auth.enabled)!} replace />
                  ) : (
                    <AuthEntryPage route={route} />
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
  const navigate = useNavigate()
  const location = useLocation()
  const breadcrumbs = getRouteBreadcrumbs(location.pathname)
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

  const navigation = navigationSections.map((section) => ({
    path: section.path,
    title: section.label,
    icon: section.icon,
    view: section.id === 'home' || section.id === 'office' ? section.id : undefined,
  }))

  const navigationExtensions = navigationSections.flatMap((section) =>
    section.subpages
      .filter((page) => page.path !== section.path)
      .filter((page) => (!page.authOnly || config.auth.enabled) &&
        (!page.capability || config.capabilities.includes(page.capability)))
      .map((page) => ({ path: page.path, title: page.label, icon: section.icon })),
  ).concat(
    globalRoutes
      .filter((route) => route.path !== '/search')
      .map((route) => ({
        path: route.path,
        title: route.title,
        icon: route.path === '/notifications' ? '♧' : '◷',
      })),
  )


  return (
    <div className={collapsed ? 'app-shell nav-collapsed' : 'app-shell'} data-v={location.pathname.startsWith('/workspace/office') ? 'office' : 'home'}>
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
          <div className="prototype-nav-section-label" aria-hidden="true">新增业务</div>
          {navigationExtensions.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              title={collapsed ? item.title : undefined}
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
        {breadcrumbs.length > 0 && (
          <nav className="app-breadcrumbs" aria-label="面包屑">
            {breadcrumbs.map((crumb, index) => (
              <span className="app-breadcrumb-item" key={`${crumb.label}-${index}`}>
                {index > 0 && <span className="app-breadcrumb-separator" aria-hidden="true">/</span>}
                {crumb.path && index < breadcrumbs.length - 1 ? <Link to={crumb.path}>{crumb.label}</Link> : <span aria-current="page">{crumb.label}</span>}
              </span>
            ))}
          </nav>
        )}
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
  version,
}: {
  route: PageRoute
  authEnabled: boolean
  capabilities: string[]
  version: string
}) {
  const routeParams = useParams<{ runId?: string; sessionId?: string; conversationId?: string }>()
  const runId = routeParams.runId ?? ''
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
  if (route.path === '/admin/access-control') return <Navigate to="/admin/rbac" replace />
  if (route.path === '/admin/organization') return <OrganizationAccessPage page="organization" />
  if (route.path === '/admin/rbac') return <OrganizationAccessPage page="rbac" />
  if (route.path === '/admin/settings') return <SystemSettingsPage config={{ auth: { enabled: authEnabled }, version, capabilities }} />
  if (route.path === '/operations/dashboard') return <OperationsDashboard />
  if (route.path === '/workspace/overview') return <HomeWorkspace authEnabled={authEnabled} />
  if (route.path === '/workspace/office') return <OfficeWorkspace />
  if (route.path === '/search')
    return (
      <GlobalSearch authEnabled={authEnabled} capabilities={capabilities} />
    )
  if (route.path === '/notifications') return <NotificationsPage />
  if (route.path === '/workspace/tasks') return <TaskCommandCenter />
  if (route.path === '/operations/tasks/timeline') return <TaskRunTimeline />
  const collaborationPage = resolveCollaborationPage(route.path, runId)
  if (collaborationPage) return collaborationPage
  if (route.path === '/operations/agents') return <AgentRuntimeList />
  if (route.path === '/operations/agents/:agentId') return <AgentDefinitionPage />
  if (route.path === '/workspace/quality-cost') return <CostQualityCenter />
  if (route.path === '/admin/ops') return <SystemOpsPage />
  if (route.path === '/operations/knowledge-bases' || route.path === '/operations/knowledge-bases/:collectionId' || route.path === '/operations/documents/:documentId') return <KnowledgeWorkbench />
  if (route.path === '/operations/approvals') return <GenericListPage title="审批中心" description="查看服务端返回且当前身份有权访问的审批请求。" apiPath="/platform/v1/approvals" refetchInterval={15000} />
  if (route.path === '/operations/approvals/:approvalId') return <ApprovalDetailPage />
  const sessionPage = resolveSessionWorkbenchPage(route.path, authEnabled)
  if (sessionPage) return sessionPage
  if (route.path === '/operations/files' || route.path === '/operations/files/:fileId') return <SessionFileWorkbench view="files" authEnabled={authEnabled} />
  if (['/operations/evaluation/datasets', '/operations/evaluation/datasets/:datasetId', '/operations/evaluation/runs', '/operations/evaluation/runs/:evaluationRunId'].includes(route.path)) return <EvaluationWorkspace />
  if (route.path === '/admin/skills') return <SkillsPage />
  if (route.path === '/admin/skills/:skillId') return <ToolSkillResourcePage kind="skill" />
  if (route.path === '/operations/tasks/:taskId') return <TaskDetailPage />
  if (route.path === '/operations/runs/:runId') return <RunTracePage />
  const adminPage = resolveAdminPage(route.path, authEnabled)
  if (adminPage) return adminPage
  if (route.path === '/admin/tools') return <ToolsRegistryPage />
  if (route.path === '/admin/tools/:toolId') return <ToolSkillResourcePage kind="tool" />
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

/** Resolve the collaboration workspace and its run-scoped deep link. */
export function resolveCollaborationPage(routePath: string, runId = ''): ReactElement | null {
  if (routePath === '/workspace/collaboration') return <AgentCollaborationCenter />
  if (routePath === '/operations/workflow-runs/:runId') return <AgentCollaborationCenter initialRunId={runId} />
  return null
}

export function resolveAdminPage(
  routePath: string,
  authEnabled: boolean,
): ReactElement | null {
  if (routePath === '/admin/agents' || routePath === '/admin/agents/:agentId') {
    return <AgentDefinitionPage />
  }
  if (
    routePath === '/admin/workflows' ||
    routePath === '/admin/workflows/:workflowId' ||
    routePath === '/admin/workflows/:workflowId/edit'
  ) {
    return <WorkflowDesignerPage />
  }
  if (
    routePath === '/admin/models/providers' ||
    routePath === '/admin/models/providers/:providerId' ||
    routePath === '/admin/models/templates' ||
    routePath === '/admin/models/templates/:templateId' ||
    routePath === '/admin/models/routing' ||
    routePath === '/admin/models/routing/:policyId'
  ) {
    return <ModelsManagementPage authEnabled={authEnabled} />
  }
  return null
}

function AuthEntryPage({ route }: { route: PageRoute }) {
  if (route.path === '/auth/login') return <LoginPage />
  if (route.path === '/auth/initialize') return <AdminInitPage />
  return <NotFoundState />
}

export default function App() {
  return <BootstrapGate />
}


/** Resolve both historical Session URLs to the single SessionFileWorkbench implementation. */
export function resolveSessionWorkbenchPage(routePath: string, authEnabled = true): ReactElement | null {
  if (routePath === '/operations/sessions' || routePath === '/operations/conversations') {
    return <SessionFileWorkbench view="sessions" authEnabled={authEnabled} />
  }
  if (routePath === '/operations/sessions/:sessionId' || routePath === '/operations/conversations/:conversationId') {
    return <SessionFileWorkbench view="sessions" authEnabled={authEnabled} />
  }
  return null
}
