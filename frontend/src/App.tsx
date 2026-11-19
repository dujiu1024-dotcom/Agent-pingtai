import { useEffect, useState } from 'react'
import {
  BrowserRouter,
  Link,
  Navigate,
  Route,
  Routes,
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
import { CostQualityCenter } from '@/features/quality-cost/CostQualityCenter'
import { KnowledgeWorkbench } from '@/features/knowledge/KnowledgeWorkbench'
import {
  TaskCommandCenter,
  TaskDetailPage,
  RunTracePage,
} from '@/features/tasks/TaskCommandCenter'
import { loadBootstrapConfig } from '@/lib/bootstrap/bootstrap'
import type { BootstrapConfig } from '@/lib/bootstrap/bootstrap'
import {
  authRoutes,
  detailRoutes,
  globalRoutes,
  pageRoutes,
  round3Routes,
} from '@/routes/route-config'
import type { PageRoute } from '@/routes/route-config'
import { HomePage } from '@/components/HomePage'
import { OfficePage } from '@/components/OfficePage'
import { WorkspaceShell } from '@/shell/WorkspaceShell'

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
          <Route element={<WorkspaceShell config={config} />}>
            <Route
              path="/"
              element={<Navigate to="/home" replace />}
            />
            <Route
              path="/app"
              element={<Navigate to="/workspace/overview" replace />}
            />
            <Route path="/home" element={<HomePage />} />
            <Route path="/office" element={<OfficePage />} />
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
            <Route path="*" element={<Navigate to="/home" replace />} />
          </Route>
        </Routes>
      </ErrorBoundary>
    </BrowserRouter>
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
  if (route.path === '/home') return <HomePage />
  if (route.path === '/office') return <OfficePage />
  if (route.path === '/workspace/overview') return <HomePage />
  if (route.path === '/workspace/office') return <OfficePage />
  if (route.path === '/search')
    return (
      <GlobalSearch authEnabled={authEnabled} capabilities={capabilities} />
    )
  if (route.path === '/notifications') return <NotificationsPage />
  if (route.path === '/workspace/tasks') return <TaskCommandCenter />
  if (route.path === '/workspace/quality-cost') return <CostQualityCenter />
  if (route.path === '/operations/knowledge-bases' || route.path === '/operations/knowledge-bases/:collectionId' || route.path === '/operations/documents/:documentId') return <KnowledgeWorkbench />
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
