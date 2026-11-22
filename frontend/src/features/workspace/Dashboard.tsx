import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import './dashboard.css'

type Task = {
  task_id: string
  title?: string
  name?: string
  description?: string
  status?: string
  created_at?: string
  updated_at?: string
  duration_ms?: number
  agent_id?: string
  priority?: string
}
type Agent = {
  item_id: string
  definition?: { name?: string; description?: string; [key: string]: unknown }
}
type Tab = 'all' | 'running' | 'approval'

const TASKS_API = '/platform/v1/workbench/tasks'
const AGENTS_API = '/platform/v1/registry/agent'
const listFrom = <T,>(value: unknown): T[] => {
  if (Array.isArray(value)) return value as T[]
  if (!value || typeof value !== 'object') return []
  const record = value as Record<string, unknown>
  for (const key of ['items', 'results', 'data']) {
    const candidate = record[key]
    if (Array.isArray(candidate)) return candidate as T[]
    if (candidate && typeof candidate === 'object') {
      const nested = candidate as Record<string, unknown>
      if (Array.isArray(nested.items)) return nested.items as T[]
    }
  }
  return []
}
const statusText = (status = ''): string => ({
  running: '运行中', executing: '运行中', queued: '排队中', pending: '待处理',
  waiting_approval: '等待审批', waiting_human: '等待审批',
  succeeded: '已完成', success: '已完成', completed: '已完成', done: '已完成',
  failed: '失败', error: '失败', paused: '已暂停', cancelled: '已取消',
}[status.toLowerCase()] ?? (status || '状态未知'))
const statusClass = (status = '') => {
  const value = status.toLowerCase()
  if (['running', 'executing'].includes(value)) return 'is-running'
  if (['queued', 'pending', 'waiting_approval', 'waiting_human'].includes(value)) return 'is-pending'
  if (['failed', 'error'].includes(value)) return 'is-failed'
  return 'is-done'
}
const taskName = (task: Task) => task.title || task.name || task.description || task.task_id
const dateLabel = (value?: string) => value ? new Date(value).toLocaleString() : '时间未提供'
const durationLabel = (ms: number) => ms < 60000 ? `${Math.round(ms / 1000)} 秒` : `${(ms / 60000).toFixed(1)} 分钟`

function MetricCard({ label, value, note, icon, accent }: {
  label: string; value: string; note: string; icon: string; accent: string
}) {
  return <article className="workspace-metric">
    <div className="workspace-metric-top"><span>{label}</span><i className={accent}>{icon}</i></div>
    <strong>{value}</strong><small>{note}</small>
  </article>
}

export function Dashboard() {
  const [tab, setTab] = useState<Tab>('all')
  const tasksQuery = useQuery({
    queryKey: ['workspace', 'dashboard', 'tasks'],
    queryFn: () => apiClient.get<unknown>(TASKS_API).then((value) => listFrom<Task>(value)),
    staleTime: 15000,
    refetchInterval: 30000,
  })
  const agentsQuery = useQuery({
    queryKey: ['workspace', 'dashboard', 'agents'],
    queryFn: () => apiClient.get<unknown>(AGENTS_API).then((value) => listFrom<Agent>(value)),
    staleTime: 60000,
    refetchInterval: 60000,
  })
  const tasks = tasksQuery.data ?? []
  const agents = agentsQuery.data ?? []
  const completed = tasks.filter((task) => ['succeeded', 'success', 'completed', 'done'].includes((task.status ?? '').toLowerCase()))
  const running = tasks.filter((task) => ['running', 'executing'].includes((task.status ?? '').toLowerCase()))
  const approvals = tasks.filter((task) => ['waiting_approval', 'waiting_human'].includes((task.status ?? '').toLowerCase()))
  const durations = tasks.map((task) => task.duration_ms).filter((value): value is number => typeof value === 'number' && value >= 0)
  const avgDuration = durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : null
  const successRate = tasks.length ? Math.round((completed.length / tasks.length) * 100) : null
  const visibleTasks = useMemo(() => {
    const selected = tab === 'running' ? running : tab === 'approval' ? approvals : tasks
    return [...selected].sort((a, b) => Date.parse(b.updated_at ?? b.created_at ?? '') - Date.parse(a.updated_at ?? a.created_at ?? '')).slice(0, 6)
  }, [tab, tasks, running.length, approvals.length])
  const trend = useMemo(() => {
    const today = new Date()
    return Array.from({ length: 7 }, (_, index) => {
      const day = new Date(today)
      day.setHours(0, 0, 0, 0)
      day.setDate(day.getDate() - (6 - index))
      const next = new Date(day)
      next.setDate(next.getDate() + 1)
      const count = tasks.filter((task) => {
        const timestamp = Date.parse(task.created_at ?? '')
        return Number.isFinite(timestamp) && timestamp >= day.getTime() && timestamp < next.getTime()
      }).length
      return { label: day.toLocaleDateString(undefined, { weekday: 'short' }), count }
    })
  }, [tasks])
  const maxTrend = Math.max(1, ...trend.map((item) => item.count))
  const activity = [...tasks].filter((task) => task.updated_at || task.created_at)
    .sort((a, b) => Date.parse(b.updated_at ?? b.created_at ?? '') - Date.parse(a.updated_at ?? a.created_at ?? '')).slice(0, 4)

  return <section className="page-content workspace-dashboard">
    <div className="workspace-welcome">
      <div><p className="workspace-eyebrow">AGENT PLATFORM / WORKSPACE</p>
        <h1>工作台</h1><p>统一查看任务执行、Agent 协作与平台运营动态</p></div>
      <Link className="workspace-primary-action" to="/workspace/tasks">创建 / 管理任务 <span>↗</span></Link>
    </div>

    <div className="workspace-metrics">
      <MetricCard label="任务总数" value={tasksQuery.isLoading ? '—' : tasks.length.toLocaleString()} note={tasksQuery.isError ? '任务数据暂不可用' : '当前 API 返回的任务'} icon="▤" accent="metric-blue" />
      <MetricCard label="成功率" value={successRate === null ? '—' : `${successRate}%`} note={successRate === null ? '暂无可计算的任务记录' : `${completed.length} 项已完成`} icon="✓" accent="metric-green" />
      <MetricCard label="平均耗时" value={avgDuration === null ? '—' : durationLabel(avgDuration)} note={durations.length ? `基于 ${durations.length} 条耗时记录` : '等待服务端耗时数据'} icon="◷" accent="metric-purple" />
      <MetricCard label="总成本" value="—" note="成本 API 尚未接入此工作台" icon="￥" accent="metric-orange" />
    </div>

    <div className="workspace-primary-grid">
      <article className="workspace-panel workspace-trend-panel">
        <div className="workspace-panel-heading"><div><h2>任务趋势</h2><p>近 7 天 · 按任务创建时间统计</p></div><span className="workspace-panel-tag">LIVE DATA</span></div>
        <div className="workspace-trend-summary"><strong>{tasks.length.toLocaleString()}</strong><span>当前返回任务</span><div><b>{running.length}</b> 运行中 <i/> <b>{completed.length}</b> 已完成</div></div>
        {tasksQuery.isError ? <div className="workspace-inline-empty">任务趋势暂不可用，请检查工作台 API</div> : tasksQuery.isLoading ? <div className="workspace-inline-empty">正在读取任务数据…</div> :
          <div className="workspace-chart" role="img" aria-label="近七天任务创建数量柱状图">
            {trend.map((item) => <div className="workspace-chart-column" key={item.label}>
              <span className="workspace-chart-value">{item.count || ''}</span>
              <i style={{ height: `${Math.max(5, item.count / maxTrend * 100)}%` }} />
              <small>{item.label}</small>
            </div>)}
          </div>}
      </article>

      <article className="workspace-panel workspace-agent-panel">
        <div className="workspace-panel-heading"><div><h2>热门 Agent</h2><p>来自 Agent Registry</p></div><Link to="/workspace/collaboration">查看全部 ↗</Link></div>
        {agentsQuery.isLoading ? <div className="workspace-inline-empty">正在读取 Agent…</div> :
          agentsQuery.isError ? <div className="workspace-inline-empty">Agent Registry 暂不可用</div> :
          agents.length ? <div className="workspace-agent-list">{agents.slice(0, 4).map((agent, index) => <div className="workspace-agent-item" key={agent.item_id}>
            <span className={`workspace-agent-avatar agent-avatar-${index}`}>{(agent.definition?.name ?? 'A').slice(0, 1)}</span>
            <span><strong>{agent.definition?.name || agent.item_id}</strong><small>{agent.definition?.description || 'Agent Registry 条目'}</small></span><i>›</i>
          </div>)}</div> : <div className="workspace-inline-empty">暂无 Agent Registry 数据</div>}
      </article>
    </div>

    <div className="workspace-secondary-grid">
      <article className="workspace-panel workspace-task-panel">
        <div className="workspace-panel-heading"><div><h2>任务列表</h2><p>从任务中心同步最新执行记录</p></div><Link to="/workspace/tasks">进入任务中心 ↗</Link></div>
        <div className="workspace-tabs" role="tablist" aria-label="任务筛选">
          {([{ id: 'all', label: '全部任务', count: tasks.length }, { id: 'running', label: '运行中', count: running.length }, { id: 'approval', label: '等待审批', count: approvals.length }] as const).map((item) =>
            <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}>{item.label}<span>{item.count}</span></button>)}
        </div>
        {tasksQuery.isLoading ? <div className="workspace-inline-empty">正在加载任务…</div> :
          tasksQuery.isError ? <div className="workspace-inline-empty">任务列表加载失败。请稍后重试或进入任务中心查看。</div> :
          visibleTasks.length ? <div className="workspace-task-list">{visibleTasks.map((task) => <Link to={`/operations/tasks/${encodeURIComponent(task.task_id)}`} className="workspace-task-row" key={task.task_id}>
            <span className="workspace-task-symbol">▣</span><span className="workspace-task-copy"><strong>{taskName(task)}</strong><small>{task.task_id} · {dateLabel(task.updated_at ?? task.created_at)}</small></span>
            <span className={`workspace-status ${statusClass(task.status)}`}>{statusText(task.status)}</span><i>›</i>
          </Link>)}</div> : <div className="workspace-inline-empty">此分类暂无任务记录</div>}
      </article>

      <article className="workspace-panel workspace-activity-panel">
        <div className="workspace-panel-heading"><div><h2>最近动态</h2><p>按任务更新时间排序</p></div><span className="workspace-panel-tag">ACTIVITY</span></div>
        {tasksQuery.isLoading ? <div className="workspace-inline-empty">正在读取动态…</div> :
          tasksQuery.isError ? <div className="workspace-inline-empty">动态数据暂不可用</div> :
          activity.length ? <div className="workspace-activity-list">{activity.map((task) => <div className="workspace-activity-item" key={task.task_id}>
            <i className={statusClass(task.status)} /><span><strong>{taskName(task)}</strong><small>{statusText(task.status)} · {dateLabel(task.updated_at ?? task.created_at)}</small></span>
          </div>)}</div> : <div className="workspace-inline-empty">暂无最近动态</div>}
      </article>
    </div>

    <div className="workspace-entry-row">
      <Link to="/workspace/collaboration"><span>⌘</span><strong>Agent 协作</strong><small>Agent 与协作运行态</small><i>↗</i></Link>
      <Link to="/admin/workflows"><span>⌁</span><strong>Workflow</strong><small>工作流设计与编排</small><i>↗</i></Link>
      <Link to="/workspace/quality-cost"><span>￥</span><strong>成本分摊</strong><small>用量、成本与质量</small><i>↗</i></Link>
      <Link to="/workspace/tasks"><span>▤</span><strong>运行任务</strong><small>任务队列与执行状态</small><i>↗</i></Link>
    </div>
  </section>
}
