import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import type { TaskRecord } from '@/features/tasks/task-model'

type RecordValue = Record<string, unknown>
const unwrap = <T,>(value: unknown): T[] => {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object') {
    const record = value as { data?: unknown; items?: unknown }
    if (Array.isArray(record.items)) return record.items as T[]
    if (Array.isArray(record.data)) return record.data as T[]
    if (record.data && typeof record.data === 'object' && Array.isArray((record.data as { items?: unknown }).items))
      return (record.data as { items: T[] }).items
  }
  return []
}
const value = (record: RecordValue, ...keys: string[]) => {
  for (const key of keys) {
    const item = record[key]
    if (typeof item === 'string' && item.trim()) return item
    if (typeof item === 'number') return String(item)
  }
  return ''
}
const statusLabel: Record<string, string> = {
  pending: '待处理', queued: '排队中', running: '运行中',
  waiting_approval: '等待审批', waiting_human: '等待人工',
  completed: '已完成', succeeded: '已完成', failed: '失败',
  cancelled: '已取消', canceled: '已取消', paused: '已暂停',
}
const statusTone = (status: string) =>
  ['running', 'queued'].includes(status) ? 'running'
    : ['waiting_approval', 'waiting_human'].includes(status) ? 'waiting'
      : ['completed', 'succeeded'].includes(status) ? 'success'
        : ['failed', 'cancelled', 'canceled'].includes(status) ? 'danger' : 'neutral'
const formatTime = (raw?: string) => {
  if (!raw) return '时间未提供'
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? '时间未提供' : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
const formatDuration = (task: TaskRecord) => {
  const ms = typeof task.duration_ms === 'number' ? task.duration_ms : undefined
  if (ms !== undefined && Number.isFinite(ms) && ms >= 0) return ms < 60000 ? `${Math.round(ms / 1000)} 秒` : `${Math.floor(ms / 60000)} 分钟`
  if (typeof task.progress === 'number') return `已完成 ${Math.max(0, Math.min(100, task.progress))}%`
  return task.updated_at ? `更新于 ${formatTime(task.updated_at)}` : '耗时未提供'
}

function TaskListPanel() {
  const [tab, setTab] = useState<'all' | 'running' | 'approval'>('running')
  const query = useQuery({
    queryKey: ['home', 'tasks'],
    queryFn: () => apiClient.get<unknown>('/workbench/tasks'),
    refetchInterval: 15000,
  })
  const tasks = useMemo(() => unwrap<TaskRecord>(query.data), [query.data])
  const filtered = tasks.filter((task) => {
    if (tab === 'running') return ['running', 'queued'].includes(task.status)
    if (tab === 'approval') return ['waiting_approval', 'waiting_human'].includes(task.status)
    return true
  }).slice(0, 3)
  return (
    <section className="home-panel home-task-panel" aria-labelledby="home-tasks-title">
      <div className="home-panel-heading">
        <div><span className="home-kicker">TASKS</span><h2 id="home-tasks-title">我的工作台</h2></div>
        <Link className="home-more" to="/workspace/tasks">全部任务 <span aria-hidden="true">→</span></Link>
      </div>
      <div className="home-tabs" role="tablist" aria-label="任务状态">
        <button type="button" role="tab" aria-selected={tab === 'all'} className={tab === 'all' ? 'is-active' : ''} onClick={() => setTab('all')}>全部任务</button>
        <button type="button" role="tab" aria-selected={tab === 'running'} className={tab === 'running' ? 'is-active' : ''} onClick={() => setTab('running')}>运行中</button>
        <button type="button" role="tab" aria-selected={tab === 'approval'} className={tab === 'approval' ? 'is-active' : ''} onClick={() => setTab('approval')}>等待审批</button>
      </div>
      {query.isPending ? <LoadingState label="正在读取任务" /> : query.isError ? (
        <ErrorState title="任务读取失败" description={query.error instanceof Error ? query.error.message : '请求失败'} onRetry={() => void query.refetch()} />
      ) : filtered.length ? (
        <div className="home-task-list">
          {filtered.map((task) => (
            <Link className="home-task-item" key={task.task_id} to={`/operations/tasks/${encodeURIComponent(task.task_id)}`}>
              <span className={`home-task-mark tone-${statusTone(task.status)}`} aria-hidden="true" />
              <span className="home-task-copy"><strong>{task.title}</strong><small>{task.task_id} · {task.assigned_agent_id ?? task.owner ?? '未分派'}</small></span>
              <span className="home-task-meta"><b className={`home-status tone-${statusTone(task.status)}`}>{statusLabel[task.status] ?? task.status}</b><small>{formatDuration(task)}</small></span>
            </Link>
          ))}
        </div>
      ) : <EmptyState title="当前没有匹配任务" description="切换状态标签，或前往任务中心查看全部任务。" />}
      <div className="home-panel-foot"><span className="home-live-dot" /> 数据来自任务服务 · 15 秒刷新</div>
    </section>
  )
}

function PopularAgents() {
  const query = useQuery({
    queryKey: ['home', 'agents'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/registry/agent'),
    refetchInterval: 30000,
  })
  const agents = useMemo(() => unwrap<RecordValue>(query.data), [query.data])
  const ranked = agents.slice(0, 3)
  return (
    <section className="home-panel home-popular-panel" aria-labelledby="home-agents-title">
      <div className="home-panel-heading">
        <div><span className="home-kicker">POPULAR AGENTS</span><h2 id="home-agents-title">热门 Agent</h2></div>
        <Link className="home-more" to="/workspace/collaboration">更多 <span aria-hidden="true">→</span></Link>
      </div>
      {query.isPending ? <LoadingState label="正在读取 Agent" /> : query.isError ? (
        <ErrorState title="Agent 列表读取失败" description={query.error instanceof Error ? query.error.message : '请求失败'} onRetry={() => void query.refetch()} />
      ) : ranked.length ? (
        <div className="home-agent-list">
          {ranked.map((agent, index) => {
            const name = value(agent, 'name', 'display_name', 'agent_name') || '未命名 Agent'
            const id = value(agent, 'agent_id', 'id') || name
            const subtitle = value(agent, 'description', 'summary', 'role') || '智能体'
            const metric = value(agent, 'run_count', 'total_runs', 'invocation_count')
            const status = value(agent, 'status', 'state') || 'registered'
            return (
              <Link className="home-agent-item" to="/workspace/collaboration" key={id}>
                <span className={`home-agent-rank rank-${index + 1}`}>{index + 1}</span>
                <span className={`home-agent-avatar avatar-${index + 1}`} aria-hidden="true">{['▥', '▤', '‹›'][index]}</span>
                <span className="home-agent-copy"><strong>{name}</strong><small>{subtitle}</small></span>
                <span className="home-agent-trailing"><b>{metric ? `${metric} 次` : status === 'registered' ? '已注册' : status}</b><small>{status === 'active' || status === 'online' ? '在线' : '可调用'}</small></span>
              </Link>
            )
          })}
        </div>
      ) : <EmptyState title="暂无已注册 Agent" description="Agent 注册后会显示在这里。" />}
      <div className="home-panel-foot">按注册服务返回顺序展示 · 使用真实注册数据</div>
    </section>
  )
}

function ActivityFeed() {
  const query = useQuery({
    queryKey: ['home', 'recent-runs'],
    queryFn: () => apiClient.get<unknown>('/workbench/runs'),
    refetchInterval: 15000,
  })
  const runs = useMemo(() => unwrap<RecordValue>(query.data).slice(0, 3), [query.data])
  return (
    <section className="home-panel home-activity-panel" aria-labelledby="home-activity-title">
      <div className="home-panel-heading">
        <div><span className="home-kicker">ACTIVITY</span><h2 id="home-activity-title">最近动态</h2></div>
        <Link className="home-more" to="/workspace/tasks">更多 <span aria-hidden="true">→</span></Link>
      </div>
      {query.isPending ? <LoadingState label="正在读取最近动态" /> : query.isError ? (
        <ErrorState title="动态读取失败" description={query.error instanceof Error ? query.error.message : '请求失败'} onRetry={() => void query.refetch()} />
      ) : runs.length ? (
        <ol className="home-activity-list">
          {runs.map((run, index) => {
            const id = value(run, 'run_id', 'id') || `run-${index}`
            const status = value(run, 'status') || 'unknown'
            const title = value(run, 'task_title', 'title', 'task_id') || '任务执行'
            const agent = value(run, 'agent_name', 'agent_id')
            const when = value(run, 'finished_at', 'updated_at', 'started_at')
            const summary = status === 'completed' || status === 'succeeded' ? `任务「${title}」运行完成`
              : status === 'failed' ? `任务「${title}」运行失败`
                : agent ? `Agent「${agent}」正在执行「${title}」` : `任务「${title}」状态更新`
            return (
              <li className="home-activity-item" key={id}>
                <span className={`home-activity-dot tone-${statusTone(status)}`} aria-hidden="true" />
                <time>{formatTime(when)}</time><span className="home-activity-text">{summary}</span>
              </li>
            )
          })}
        </ol>
      ) : <EmptyState title="暂无最近动态" description="任务开始执行后，最新运行记录会显示在这里。" />}
      <div className="home-panel-foot">来源：最近 Run 记录</div>
    </section>
  )
}

export function HomeWorkspace() {
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [dark, setDark] = useState(false)
  return (
    <section className={dark ? 'page-content home-workspace home-workspace-dark' : 'page-content home-workspace'}>
      <div className="home-welcome">
        <div className="home-welcome-copy">
          <span className="home-kicker">AGENT PLATFORM / WORKSPACE</span>
          <h1>欢迎回来！</h1>
          <p>让 AI 赋能每一个工作场景</p>
        </div>
        <div className="home-welcome-actions">
          <button type="button" className="home-icon-button" aria-label="切换主题" title="切换主题" onClick={() => setDark((current) => !current)}>{dark ? '☾' : '☀'}</button>
          <button type="button" className="home-assistant-button" onClick={() => setAssistantOpen((open) => !open)}>✦ 你好！我是你的 Agent 助手</button>
        </div>
      </div>
      <div className="home-shortcut-row">
        <Link to="/workspace/collaboration"><span>✧</span><b>Agent 协作</b><small>查看智能体</small></Link>
        <Link to="/workspace/tasks"><span>▶</span><b>运行任务</b><small>进入任务中心</small></Link>
        <Link to="/workspace/collaboration"><span>⌘</span><b>管理 Workflow</b><small>查看协作流程</small></Link>
      </div>
      {assistantOpen && <div className="home-assistant-popover" role="status"><b>Agent 助手</b><p>助手入口已就绪。对话能力接入后可在此开始协作。</p><button type="button" onClick={() => setAssistantOpen(false)}>关闭</button></div>}
      <div className="home-panels-grid">
        <TaskListPanel />
        <PopularAgents />
        <ActivityFeed />
      </div>
    </section>
  )
}
