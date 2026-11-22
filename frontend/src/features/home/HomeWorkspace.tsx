import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { unwrapList } from '@/features/tasks/task-model'
import type { RunRecord, TaskRecord } from '@/features/tasks/task-model'

type AgentRecord = {
  agent_id?: string
  id?: string
  name?: string
  display_name?: string
  description?: string
  status?: string
  run_count?: number
  total_runs?: number
  invocation_count?: number
}
type TaskTab = 'running' | 'completed' | 'approval'

const statusText: Record<string, string> = {
  pending: '待处理',
  queued: '排队中',
  running: '运行中',
  waiting_approval: '等待审批',
  waiting_human: '等待人工',
  completed: '已完成',
  succeeded: '已完成',
  failed: '执行失败',
  cancelled: '已取消',
  canceled: '已取消',
  paused: '已暂停',
}
const statusClass = (status: string) => {
  if (status === 'running' || status === 'queued') return 'home-state-running'
  if (status === 'waiting_approval' || status === 'waiting_human') return 'home-state-waiting'
  if (status === 'completed' || status === 'succeeded') return 'home-state-completed'
  if (status === 'failed' || status === 'cancelled' || status === 'canceled') return 'home-state-failed'
  return 'home-state-neutral'
}
const clock = (raw?: string) => {
  if (!raw) return '—'
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
const taskDuration = (task: TaskRecord) => {
  const ms = task.duration_ms
  if (typeof ms === 'number' && Number.isFinite(ms) && ms >= 0) {
    return ms < 60_000 ? `耗时 ${Math.round(ms / 1000)} 秒` : `耗时 ${Math.floor(ms / 60_000)} 分钟`
  }
  if (typeof task.progress === 'number') return `已完成 ${Math.max(0, Math.min(100, task.progress))}%`
  return task.updated_at ? `更新于 ${clock(task.updated_at)}` : '耗时 —'
}

function TaskListPanel() {
  const [tab, setTab] = useState<TaskTab>('running')
  const query = useQuery({
    queryKey: ['home', 'tasks'],
    queryFn: () => apiClient.get<unknown>('/workbench/tasks').then(unwrapList<TaskRecord>),
    refetchInterval: 15_000,
  })
  const tasks = query.data ?? []
  const runningCount = tasks.filter((task) => ['running', 'queued'].includes(task.status)).length
  const approvalCount = tasks.filter((task) => ['waiting_approval', 'waiting_human'].includes(task.status)).length
  const filtered = tasks.filter((task) => {
    if (tab === 'running') return ['running', 'queued'].includes(task.status)
    if (tab === 'completed') return ['completed', 'succeeded'].includes(task.status)
    return ['waiting_approval', 'waiting_human'].includes(task.status)
  }).slice(0, 3)

  return (
    <section className="home-panel home-task-panel" aria-labelledby="home-task-heading">
      <header className="home-panel-heading">
        <h2 id="home-task-heading">我的工作台</h2>
        <Link to="/workspace/tasks" className="home-panel-more">全部任务 <span aria-hidden="true">→</span></Link>
      </header>
      <div className="home-task-tabs" role="tablist" aria-label="任务状态">
        <button type="button" role="tab" aria-selected={tab === 'running'} className={tab === 'running' ? 'is-selected' : ''} onClick={() => setTab('running')}>进行中 ({runningCount})</button>
        <button type="button" role="tab" aria-selected={tab === 'completed'} className={tab === 'completed' ? 'is-selected' : ''} onClick={() => setTab('completed')}>已完成</button>
        <button type="button" role="tab" aria-selected={tab === 'approval'} className={tab === 'approval' ? 'is-selected' : ''} onClick={() => setTab('approval')}>待审批 ({approvalCount})</button>
      </div>
      {query.isPending ? <LoadingState label="正在加载任务" /> : query.isError ? (
        <ErrorState title="任务加载失败" description={query.error instanceof Error ? query.error.message : '请求失败'} onRetry={() => void query.refetch()} />
      ) : filtered.length ? (
        <div className="home-task-rows">
          {filtered.map((task) => (
            <Link className="home-task-row" key={task.task_id} to={`/operations/tasks/${encodeURIComponent(task.task_id)}`}>
              <span className="home-task-accent" aria-hidden="true" />
              <span className="home-task-main"><strong>{task.title}</strong><small>{task.assigned_agent_id ?? task.owner ?? '未分派'}</small></span>
              <span className="home-task-trailing"><b className={`home-state ${statusClass(task.status)}`}>{statusText[task.status] ?? task.status}</b><small>{taskDuration(task)}</small></span>
            </Link>
          ))}
        </div>
      ) : <EmptyState title="暂无匹配任务" description="该状态下暂时没有任务记录。" />}
      <footer className="home-panel-foot"><span className="home-live-indicator" /> 任务服务实时数据 · 15 秒刷新</footer>
    </section>
  )
}

function PopularAgents() {
  const query = useQuery({
    queryKey: ['home', 'popular-agents'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/registry/agent').then(unwrapList<AgentRecord>),
    refetchInterval: 30_000,
  })
  const runCount = (agent: AgentRecord) =>
    [agent.run_count, agent.total_runs, agent.invocation_count].find(
      (value): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0,
    )
  const agents = useMemo(() => [...(query.data ?? [])].sort((a, b) =>
    (runCount(b) ?? -1) - (runCount(a) ?? -1),
  ).slice(0, 3), [query.data])
  const maxRunCount = Math.max(0, ...agents.map(runCount).filter((value): value is number => value !== undefined))

  return (
    <section className="home-panel home-popular-panel" aria-labelledby="home-popular-heading">
      <header className="home-panel-heading">
        <h2 id="home-popular-heading">热门 Agent</h2>
        <Link to="/workspace/collaboration" className="home-panel-more">更多 <span aria-hidden="true">→</span></Link>
      </header>
      {query.isPending ? <LoadingState label="正在加载 Agent" /> : query.isError ? (
        <ErrorState title="Agent 加载失败" description={query.error instanceof Error ? query.error.message : '请求失败'} onRetry={() => void query.refetch()} />
      ) : agents.length ? (
        <ol className="home-agent-rows">
          {agents.map((agent, index) => {
            const name = agent.name ?? agent.display_name ?? '未命名 Agent'
            const count = runCount(agent)
            const id = agent.agent_id ?? agent.id ?? name
            return (
              <li key={id}>
                <Link to="/workspace/collaboration" className="home-agent-row">
                  <span className={`home-agent-rank rank-${index + 1}`}>{index + 1}</span>
                  <span className={`home-agent-icon agent-icon-${index + 1}`} aria-hidden="true">{['▥', '▤', '‹›'][index]}</span>
                  <span className="home-agent-main"><strong>{name}</strong><small>{agent.description ?? '智能体'}</small></span>
                  <span className="home-agent-usage">
                    {typeof count === 'number' ? (
                      <>
                        <span className="home-agent-usage-track" aria-hidden="true">
                          <span style={{ width: `${maxRunCount ? Math.max(4, (count / maxRunCount) * 100) : 0}%` }} />
                        </span>
                        <span>{count.toLocaleString()} 次</span>
                      </>
                    ) : agent.status ?? '已注册'}
                  </span>
                </Link>
              </li>
            )
          })}
        </ol>
      ) : <EmptyState title="暂无已注册 Agent" description="注册的 Agent 将显示在这里。" />}
      <footer className="home-panel-foot">按真实调用量排序；无调用量时按注册顺序展示</footer>
    </section>
  )
}

function ActivityFeed() {
  const query = useQuery({
    queryKey: ['home', 'recent-activity'],
    queryFn: () => apiClient.get<unknown>('/workbench/runs').then(unwrapList<RunRecord>),
    refetchInterval: 15_000,
  })
  const runs = useMemo(() => [...(query.data ?? [])].sort((a, b) =>
    Date.parse(b.finished_at ?? b.started_at ?? '') - Date.parse(a.finished_at ?? a.started_at ?? ''),
  ).slice(0, 3), [query.data])

  return (
    <section className="home-panel home-activity-panel" aria-labelledby="home-activity-heading">
      <header className="home-panel-heading">
        <h2 id="home-activity-heading">最近动态</h2>
        <Link to="/workspace/tasks" className="home-panel-more">更多 <span aria-hidden="true">→</span></Link>
      </header>
      {query.isPending ? <LoadingState label="正在加载最近动态" /> : query.isError ? (
        <ErrorState title="动态加载失败" description={query.error instanceof Error ? query.error.message : '请求失败'} onRetry={() => void query.refetch()} />
      ) : runs.length ? (
        <ol className="home-activity-rows">
          {runs.map((run) => {
            const label = run.status === 'completed' || run.status === 'succeeded'
              ? `任务「${run.task_id}」运行完成`
              : run.status === 'failed'
                ? `任务「${run.task_id}」运行失败`
                : `任务「${run.task_id}」状态更新`
            return (
              <li key={run.run_id}>
                <span className={`home-activity-dot ${statusClass(run.status)}`} aria-hidden="true" />
                <time>{clock(run.finished_at ?? run.started_at)}</time>
                <span className="home-activity-label">{label}</span>
              </li>
            )
          })}
        </ol>
      ) : <EmptyState title="暂无最近动态" description="任务运行记录会显示在这里。" />}
      <footer className="home-panel-foot">来源：最近 Run 记录</footer>
    </section>
  )
}

const quickEntries = [
  { title: 'Agent 协作', detail: '管理智能体与协作', icon: '✧', to: '/workspace/collaboration' },
  { title: 'Workflow', detail: '设计自动化流程', icon: '⌘', to: '/admin/workflows' },
  { title: '成本分摊', detail: '查看成本与用量', icon: '¥', to: '/workspace/quality-cost' },
  { title: '运行任务', detail: '进入任务中心', icon: '▶', to: '/workspace/tasks' },
]

export function HomeWorkspace() {
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [dark, setDark] = useState(false)
  return (
    <section className={dark ? 'page-content home-workspace home-workspace-dark' : 'page-content home-workspace'}>
      <div className="home-workspace-layout">
        <div className="home-welcome">
          <div className="home-welcome-copy">
            <span className="home-eyebrow">AGENT PLATFORM / WORKSPACE</span>
            <h1>欢迎回来！</h1>
            <p>让 AI 赋能每一个工作场景</p>
          </div>
          <div className="home-welcome-actions">
            <button type="button" className="home-theme-button" onClick={() => setDark((value) => !value)} aria-label="切换主题" title="主题">{dark ? '☾' : '☀'}</button>
            <button type="button" className="home-assistant-button" onClick={() => setAssistantOpen((value) => !value)}>✦ 你好！我是你的 Agent 助手</button>
          </div>
          {assistantOpen && <div className="home-assistant-panel" role="status"><strong>Agent 助手</strong><p>助手对话能力尚未接入；入口已保留。</p><button type="button" onClick={() => setAssistantOpen(false)}>关闭</button></div>}
        </div>
        <div className="home-task-slot"><TaskListPanel /></div>
        <div className="home-lower-panels"><PopularAgents /><ActivityFeed /></div>
      </div>
      <section className="home-quick-entry" aria-label="快捷入口">
        <header><h2>快捷入口</h2><span>QUICK ACCESS</span></header>
        <div className="home-quick-grid">
          {quickEntries.map((entry) => (
            <Link to={entry.to} className="home-quick-card" key={entry.to}>
              <span aria-hidden="true">{entry.icon}</span>
              <strong>{entry.title}</strong>
              <small>{entry.detail}</small>
              <b aria-hidden="true">→</b>
            </Link>
          ))}
        </div>
      </section>
    </section>
  )
}
