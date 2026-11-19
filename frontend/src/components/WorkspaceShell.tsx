import { useEffect } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useUiStore } from '@/stores/ui-store'
import type { BootstrapConfig } from '@/lib/bootstrap/bootstrap'
import { Sidebar } from '@/components/Sidebar'
import { Topbar } from '@/components/Topbar'

type WorkspaceShellProps = { config: BootstrapConfig }

const pageTitles: Record<string, string> = {
  '/home': '工作台',
  '/office': '3D 办公室',
  '/workspace/overview': '工作台',
  '/workspace/office': '3D 办公室',
  '/workspace/collaboration': 'Agent 协作',
  '/admin/workflows': 'Workflow',
  '/workspace/quality-cost': '成本分摊',
  '/workspace/tasks': '任务中心',
  '/admin/settings': '系统管理',
  '/notifications': '通知',
  '/search': '搜索',
}

export function WorkspaceShell({ config }: WorkspaceShellProps) {
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

  const title = pageTitles[location.pathname] ?? '工作空间'

  return (
    <div className={collapsed ? 'r3-app-shell r3-nav-collapsed' : 'r3-app-shell'}>
      <Sidebar
        collapsed={collapsed}
        authEnabled={config.auth.enabled}
        version={config.version}
        onToggle={toggleSidebar}
      />
      <main className="r3-main-content">
        <Topbar title={title} />
        <Outlet />
      </main>
    </div>
  )
}
