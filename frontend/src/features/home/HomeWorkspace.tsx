import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Bot, Workflow, Wallet, Play, Sparkles, Sun, Moon } from 'lucide-react'
import { apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { formatDuration, resolveAgentId, resolveTaskTitle, unwrapList } from '@/features/tasks/task-model'
import type { TaskRecord } from '@/features/tasks/task-model'
import './HomeWorkspace.css'

type AgentRegistryItem = {
  item_id: string
  status?: string
  definition?: { name?: string; description?: string; [key: string]: unknown }
  created_at?: string | null
  published_at?: string | null
  [key: string]: unknown
}
type TaskTab = 'all' | 'running' | 'approval'
type AgentCardData = { id: string; name: string; description: string; status: string; runs: number }
type ApprovalRecord = { approval_id: string; action: string; resource: string; status: string }
type HomeActivityEvent = { id: string; kind: 'agent' | 'workflow' | 'task' | 'approval'; title: string; detail: string; at: string | null; href: string }

const RUNNING = new Set(['running', 'executing', 'in_progress', 'queued', 'pending'])
const APPROVAL = new Set(['waiting_approval', 'waiting_human'])
const COMPLETED = new Set(['completed', 'succeeded', 'success', 'done'])
const statusLabel: Record<string, string> = {
  running: '运行中', executing: '运行中', in_progress: '运行中', queued: '排队中',
  pending: '等待执行', waiting_approval: '等待审批', waiting_human: '等待人工',
  completed: '已完成', succeeded: '已完成', success: '已完成', done: '已完成',
  failed: '执行失败', cancelled: '已取消', canceled: '已取消', paused: '已暂停',
}
const statusTone = (status: string) => {
  if (RUNNING.has(status)) return 'running'
  if (APPROVAL.has(status)) return 'waiting'
  if (COMPLETED.has(status)) return 'success'
  if (['failed', 'cancelled', 'canceled'].includes(status)) return 'danger'
  return 'neutral'
}
const formatClock = (raw?: string | null) => {
  if (!raw) return '时间未提供'
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? '时间未提供' : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
export function filterHomeTasks(tasks: TaskRecord[], tab: TaskTab): TaskRecord[] {
  if (tab === 'running') return tasks.filter((task) => RUNNING.has(task.status))
  if (tab === 'approval') return tasks.filter((task) => APPROVAL.has(task.status))
  return tasks
}
export function rankHomeAgents(agents: AgentRegistryItem[], tasks: TaskRecord[]): AgentCardData[] {
  const runIdsByAgent = new Map<string, Set<string>>()
  for (const task of tasks) {
    if (!task.agent_id || !task.run_id) continue
    const runIds = runIdsByAgent.get(task.agent_id) ?? new Set<string>()
    runIds.add(task.run_id)
    runIdsByAgent.set(task.agent_id, runIds)
  }
  return agents.map((agent) => ({
    id: agent.item_id,
    name: typeof agent.definition?.name === 'string' && agent.definition.name.trim() ? agent.definition.name : agent.item_id,
    description: typeof agent.definition?.description === 'string' ? agent.definition.description : '已登记的智能体',
    status: typeof agent.status === 'string' ? agent.status : 'registered',
    runs: runIdsByAgent.get(agent.item_id)?.size ?? 0,
  })).sort((a, b) => b.runs - a.runs || a.name.localeCompare(b.name, 'zh-CN')).slice(0, 6)
}

export function buildHomeActivityFeed(
  tasks: TaskRecord[],
  agents: AgentRegistryItem[],
  workflows: AgentRegistryItem[],
  approvals: ApprovalRecord[],
): HomeActivityEvent[] {
  const events: HomeActivityEvent[] = []
  for (const task of tasks) {
    if (!COMPLETED.has(task.status.toLowerCase())) continue
    events.push({
      id: 'task:' + task.task_id, kind: 'task', title: '任务完成',
      detail: resolveTaskTitle(task) + ' · ' + (task.agent_id ?? '未关联 Agent'),
      at: task.finished_at ?? task.updated_at ?? task.created_at ?? null,
      href: '/operations/tasks/' + encodeURIComponent(task.task_id),
    })
  }
  for (const agent of agents) {
    const at = agent.published_at ?? agent.created_at ?? null
    if (!at) continue
    const name = typeof agent.definition?.name === 'string' ? agent.definition.name : agent.item_id
    events.push({
      id: 'agent:' + agent.item_id + ':' + at, kind: 'agent', title: 'Agent 更新',
      detail: name + ' · ' + agent.item_id, at,
      href: '/admin/agents/' + encodeURIComponent(agent.item_id),
    })
  }
  for (const workflow of workflows) {
    const at = workflow.published_at ?? workflow.created_at ?? null
    if (!at) continue
    const name = typeof workflow.definition?.name === 'string' ? workflow.definition.name : workflow.item_id
    events.push({
      id: 'workflow:' + workflow.item_id + ':' + at, kind: 'workflow', title: 'Workflow 新增',
      detail: name + ' · ' + workflow.item_id, at,
      href: '/admin/workflows/' + encodeURIComponent(workflow.item_id),
    })
  }
  for (const approval of approvals) {
    if (approval.status.toLowerCase() !== 'pending') continue
    events.push({
      id: 'approval:' + approval.approval_id, kind: 'approval', title: '审批已提交',
      detail: approval.action + ' · ' + approval.resource, at: null,
      href: '/operations/approvals',
    })
  }
  return events.sort((a, b) => (Date.parse(b.at ?? '') || 0) - (Date.parse(a.at ?? '') || 0)).slice(0, 8)
}

function TaskListPanel() {
  const [tab, setTab] = useState<TaskTab>('all')
  const query = useQuery({
    queryKey: ['home-workspace', 'tasks'],
    queryFn: async () => unwrapList<TaskRecord>(await apiClient.get<unknown>('/platform/v1/workbench/tasks')),
    refetchInterval: 15_000,
    retry: false,
  })
  const tasks = query.data ?? []
  const filtered = filterHomeTasks(tasks, tab).slice(0, 5)
  const tabs: { id: TaskTab; label: string }[] = [
    { id: 'all', label: '全部' }, { id: 'running', label: '运行中' }, { id: 'approval', label: '等待审批' },
  ]
  return (
    <section className="home-r3-panel home-r3-task-panel" aria-labelledby="home-r3-task-title">
      <header className="home-r3-panel-heading">
        <div><span className="home-r3-eyebrow">MY WORK</span><h2 id="home-r3-task-title">任务列表</h2></div>
        <Link className="home-r3-more" to="/workspace/tasks">全部任务 <ArrowRight size={14} /></Link>
      </header>
      <div className="home-r3-tabs" role="tablist" aria-label="任务状态筛选">
        {tabs.map((item) => (
          <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? 'is-active' : ''} onClick={() => setTab(item.id)}>{item.label}{item.id === 'running' ? ` ${tasks.filter((task) => RUNNING.has(task.status)).length}` : item.id === 'approval' ? ` ${tasks.filter((task) => APPROVAL.has(task.status)).length}` : ''}</button>
        ))}
      </div>
      {query.isPending ? <LoadingState label="正在读取任务" /> : query.isError ? (
        <ErrorState title="任务读取失败" description={query.error instanceof Error ? query.error.message : '任务服务请求失败'} onRetry={() => void query.refetch()} />
      ) : filtered.length ? (
        <ul className="home-r3-task-list">
          {filtered.map((task) => (
            <li key={task.task_id}>
              <Link to={`/operations/tasks/${encodeURIComponent(task.task_id)}`} className="home-r3-task-row">
                <span className={`home-r3-state-dot tone-${statusTone(task.status)}`} aria-hidden="true" />
                <span className="home-r3-task-copy"><strong>{resolveTaskTitle(task)}</strong><small>{task.task_id} · {resolveAgentId(task) ?? '未分配 Agent'}</small></span>
                <span className="home-r3-task-meta"><b className={`home-r3-status tone-${statusTone(task.status)}`}>{statusLabel[task.status] ?? task.status}</b><small>{task.duration_ms != null ? formatDuration(task.duration_ms) : task.started_at ? '执行中' : '耗时未提供'}</small></span>
              </Link>
            </li>
          ))}
        </ul>
      ) : <EmptyState title="暂无匹配任务" description="该筛选条件下没有任务记录。" />}
      <footer className="home-r3-panel-foot"><span className="home-r3-live-dot" />任务服务数据 · 15 秒刷新</footer>
    </section>
  )
}

function PopularAgents() {
  const agentsQuery = useQuery({
    queryKey: ['home-workspace', 'agents'],
    queryFn: async () => unwrapList<AgentRegistryItem>(await apiClient.get<unknown>('/platform/v1/registry/agent')),
    refetchInterval: 30_000,
    retry: false,
  })
  const tasksQuery = useQuery({
    queryKey: ['home-workspace', 'tasks'],
    queryFn: async () => unwrapList<TaskRecord>(await apiClient.get<unknown>('/platform/v1/workbench/tasks')),
    refetchInterval: 15_000,
    retry: false,
  })
  const ranked = useMemo(() => rankHomeAgents(agentsQuery.data ?? [], tasksQuery.data ?? []), [agentsQuery.data, tasksQuery.data])
  const loading = agentsQuery.isPending || tasksQuery.isPending
  const error = agentsQuery.error ?? tasksQuery.error
  if (loading) return <section className="home-r3-panel"><LoadingState label="正在读取热门 Agent" /></section>
  if (error) return <section className="home-r3-panel"><ErrorState title="热门 Agent 读取失败" description={error instanceof Error ? error.message : 'Agent 服务请求失败'} onRetry={() => { void agentsQuery.refetch(); void tasksQuery.refetch() }} /></section>
  return (
    <section className="home-r3-panel home-r3-agent-panel" aria-labelledby="home-r3-agent-title">
      <header className="home-r3-panel-heading">
        <div><span className="home-r3-eyebrow">POPULAR AGENTS</span><h2 id="home-r3-agent-title">热门 Agent</h2></div>
        <Link className="home-r3-more" to="/workspace/collaboration">查看全部 <ArrowRight size={14} /></Link>
      </header>
      {ranked.length ? <div className="home-r3-agent-grid">
        {ranked.map((agent, index) => (
          <Link className="home-r3-agent-card" to="/workspace/collaboration" key={agent.id}>
            <span className={`home-r3-agent-avatar avatar-${index % 4}`}><Bot size={20} /></span>
            <span className="home-r3-agent-info"><strong>{agent.name}</strong><small>{agent.description}</small></span>
            <span className="home-r3-agent-state"><i className={agent.status === 'active' || agent.status === 'online' ? 'is-online' : ''} />{agent.status === 'active' || agent.status === 'online' ? '在线' : '已登记'}</span>
            <span className="home-r3-agent-usage">{agent.runs} 次近期运行</span>
          </Link>
        ))}
      </div> : <EmptyState title="暂无已登记 Agent" description="注册中心返回的 Agent 会展示在这里。" />}
      <footer className="home-r3-panel-foot">运行数按任务服务返回的非空 Run ID 去重统计；仅统计当前返回记录</footer>
    </section>
  )
}

function ActivityFeed({ authEnabled }: { authEnabled: boolean }) {
  const tasksQuery = useQuery({
    queryKey: ['home-workspace', 'tasks'],
    queryFn: async () => unwrapList<TaskRecord>(await apiClient.get<unknown>('/platform/v1/workbench/tasks')),
    refetchInterval: 15_000,
    retry: false,
  })
  const agentsQuery = useQuery({
    queryKey: ['home-workspace', 'agents'],
    queryFn: async () => unwrapList<AgentRegistryItem>(await apiClient.get<unknown>('/platform/v1/registry/agent')),
    refetchInterval: 30_000,
    retry: false,
  })
  const workflowsQuery = useQuery({
    queryKey: ['home-workspace', 'workflows'],
    queryFn: async () => unwrapList<AgentRegistryItem>(await apiClient.get<unknown>('/platform/v1/registry/workflow')),
    refetchInterval: 30_000,
    retry: false,
  })
  const approvalsQuery = useQuery({
    queryKey: ['home-workspace', 'approvals'],
    queryFn: async () => unwrapList<ApprovalRecord>(await apiClient.get<unknown>('/platform/v1/approvals?limit=20')),
    refetchInterval: 15_000,
    retry: false,
    enabled: authEnabled,
  })
  const queries = [tasksQuery, agentsQuery, workflowsQuery, approvalsQuery]
  const events = useMemo(() => buildHomeActivityFeed(
    tasksQuery.data ?? [], agentsQuery.data ?? [], workflowsQuery.data ?? [], approvalsQuery.data ?? [],
  ), [tasksQuery.data, agentsQuery.data, workflowsQuery.data, approvalsQuery.data])
  const pending = [tasksQuery, agentsQuery, workflowsQuery].some((query) => query.isPending)
  const errors = queries.filter((query) => query.isError)
  const refresh = () => queries.forEach((query) => void query.refetch())
  return (
    <section className="home-r3-panel home-r3-activity-panel" aria-labelledby="home-r3-activity-title">
      <header className="home-r3-panel-heading">
        <div><span className="home-r3-eyebrow">RECENT ACTIVITY</span><h2 id="home-r3-activity-title">最近动态</h2></div>
        <Link className="home-r3-more" to="/workspace/tasks">运行记录 <ArrowRight size={14} /></Link>
      </header>
      {pending && !events.length ? <LoadingState label="正在读取最近动态" /> : events.length ? (
        <>
          <ol className="home-r3-timeline">
            {events.map((event) => (
              <li key={event.id}>
                <span className={`home-r3-timeline-dot tone-${event.kind === 'task' ? 'success' : event.kind === 'approval' ? 'waiting' : 'neutral'}`} />
                <time>{formatClock(event.at)}</time>
                <span className="home-r3-timeline-copy"><strong>{event.title}</strong><small>{event.detail}</small></span>
                <Link to={event.href} aria-label={`查看${event.title}`}>查看</Link>
              </li>
            ))}
          </ol>
          {errors.length > 0 && <p className="home-r3-feed-note">部分动态源暂不可用（{errors.length}）；已展示成功读取的真实记录。</p>}
        </>
      ) : errors.length ? (
        <ErrorState title="动态暂不可用" description="任务、Agent、Workflow 或审批数据源请求失败。" onRetry={refresh} />
      ) : <EmptyState title="暂无最近动态" description="Agent、Workflow、任务或审批产生记录后，会出现在此时间线。" />}
      <footer className="home-r3-panel-foot">来源：任务、Agent/Workflow 注册中心；审批动态仅在权限模式下读取</footer>
    </section>
  )
}

const quickEntries = [
  { title: 'Agent 协作', detail: '管理智能体与协作', to: '/workspace/collaboration', icon: <Bot size={19} /> },
  { title: 'Workflow', detail: '设计自动化流程', to: '/admin/workflows', icon: <Workflow size={19} /> },
  { title: '成本分摊', detail: '查看成本与用量', to: '/workspace/quality-cost', icon: <Wallet size={19} /> },
  { title: '运行任务', detail: '进入任务中心', to: '/workspace/tasks', icon: <Play size={19} /> },
]

export function HomeWorkspace({ authEnabled }: { authEnabled: boolean }) {
  const [dark, setDark] = useState(false)
  return (
    <section className={dark ? 'page-content home-r3 home-r3-dark' : 'page-content home-r3'}>
      <header className="home-r3-welcome">
        <div><span className="home-r3-eyebrow">AGENT PLATFORM / WORKSPACE</span><h1>欢迎回来！</h1><p>你好！今天想让 Agent 帮你完成什么？</p></div>
        <div className="home-r3-welcome-actions">
          <button type="button" className="home-r3-theme-button" onClick={() => setDark((value) => !value)} aria-label="切换主题" title="主题"><Sun size={17} /><Moon size={15} /></button>
          <Link className="home-r3-assistant" to="/workspace/quickstart"><Sparkles size={16} />你好！我是你的 Agent 助手</Link>
        </div>
      </header>
      <div className="home-r3-main-grid">
        <TaskListPanel />
        <div className="home-r3-side-stack"><PopularAgents /><ActivityFeed authEnabled={authEnabled} /></div>
      </div>
      <section className="home-r3-quick" aria-labelledby="home-r3-quick-title">
        <header><div><span className="home-r3-eyebrow">QUICK ACCESS</span><h2 id="home-r3-quick-title">快捷入口</h2></div></header>
        <div className="home-r3-quick-grid">
          {quickEntries.map((entry) => <Link to={entry.to} key={entry.to} className="home-r3-quick-card"><span>{entry.icon}</span><strong>{entry.title}</strong><small>{entry.detail}</small><ArrowRight size={15} /></Link>)}
        </div>
      </section>
    </section>
  )
}
