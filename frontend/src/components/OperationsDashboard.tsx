import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Activity,
  Bot,
  BookOpen,
  ChartLine,
  Inbox,
  ListTodo,
  MessageSquare,
} from 'lucide-react'

import { ApiError, apiClient } from '@/lib/api'

type UsageEvent = {
  event_id: string
  run_id: string
  model_key: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: string
  agent_id: string | null
  runtime_ms: number
  source: string
  occurred_at: string
}

type UsageSummary = {
  events: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  runtime_ms: number
  estimated_cost_usd: string
  provider_reported_cost_usd: string
  reconciled_event_cost_usd: string
  by_model: Record<
    string,
    {
      events: number
      input_tokens: number
      output_tokens: number
      cost_usd: string
    }
  >
  by_agent: Record<
    string,
    {
      events: number
      runtime_ms: number
      input_tokens: number
      output_tokens: number
      cost_usd: string
    }
  >
}

type UsageResponse = { items: UsageEvent[] }
type HealthResponse = { status: string; [key: string]: unknown }
function rangeStart(days: number): Date {
  const value = new Date()
  value.setDate(value.getDate() - days)
  return value
}

function queryRange(days: number, modelKey?: string): string {
  const end = new Date()
  const start = rangeStart(days)
  const params = new URLSearchParams({
    start: start.toISOString(),
    end: end.toISOString(),
  })
  if (modelKey) params.set('model_key', modelKey)
  return params.toString()
}

function money(value: string | undefined): string {
  if (value === undefined) return '—'
  const amount = Number(value)
  return Number.isFinite(amount)
    ? new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency: 'USD',
        maximumFractionDigits: 4,
      }).format(amount)
    : '—'
}

function number(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat().format(value)
}

function requestId(error: unknown): string | undefined {
  return error instanceof ApiError ? error.requestId : undefined
}

type KpiTone = 'blue' | 'violet' | 'green' | 'amber'

function KpiCard({
  to,
  label,
  value,
  note,
  icon,
  tone,
}: {
  to: string
  label: string
  value: string
  note: string
  icon: ReactNode
  tone: KpiTone
}) {
  return (
    <Link className={`kpi-card tone-${tone}`} to={to}>
      <span className="kpi-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="kpi-label">{label}</span>
      <div className="kpi-figure">
        <strong className="kpi-value">{value}</strong>
        <small className="kpi-note">{note}</small>
      </div>
    </Link>
  )
}

function BusinessMark() {
  return (
    <span className="home-mark" aria-hidden="true">
      <svg width="58" height="54" viewBox="0 0 26 24">
        <defs>
          <linearGradient id="home-brand-gradient" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#6aa4ff" />
            <stop offset="1" stopColor="#6d4af0" />
          </linearGradient>
        </defs>
        <path
          d="M13 1 25 23h-5.5L13 11 6.5 23H1z"
          fill="url(#home-brand-gradient)"
        />
      </svg>
    </span>
  )
}

function QueryError({
  error,
  onRetry,
}: {
  error: unknown
  onRetry: () => void
}) {
  const id = requestId(error)
  return (
    <div className="ops-query-error" role="alert">
      <span>数据暂不可用</span>
      {id && <code>request_id: {id}</code>}
      <button type="button" onClick={onRetry}>
        重试
      </button>
    </div>
  )
}

export function Dashboard() {
  const [days, setDays] = useState(7)
  const [taskTab, setTaskTab] = useState<'pending' | 'done'>('pending')
  const query = useMemo(() => queryRange(days), [days])
  const usageQuery = useQuery({
    queryKey: ['operations-dashboard', 'usage', query],
    queryFn: () =>
      apiClient.get<UsageResponse>(`/platform/v1/quotas/usage?${query}`),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  })
  const summaryQuery = useQuery({
    queryKey: ['operations-dashboard', 'summary', query],
    queryFn: () =>
      apiClient.get<UsageSummary>(`/platform/v1/quotas/usage/summary?${query}`),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  })
  const healthQuery = useQuery({
    queryKey: ['operations-dashboard', 'capacity'],
    queryFn: () =>
      apiClient.get<HealthResponse>(
        '/platform/v1/observability/health/capacity',
      ),
    refetchInterval: 30_000,
    retry: false,
  })
  // Fallback 数据源（当 quota/observability API 不可用时）
  const sessionsQuery = useQuery({
    queryKey: ['dash-fallback', 'sessions'],
    queryFn: () => apiClient.get<{ items: unknown[] }>('/platform/v1/sessions'),
    refetchInterval: 30_000,
    retry: false,
  })
  const agentsQuery = useQuery({
    queryKey: ['dash-fallback', 'agents'],
    queryFn: () =>
      apiClient.get<{ items: unknown[] }>('/platform/v1/registry/agents'),
    refetchInterval: 30_000,
    retry: false,
  })
  const tasksQuery = useQuery({
    queryKey: ['dash-fallback', 'tasks'],
    queryFn: () =>
      apiClient.get<{ items: unknown[] }>('/platform/v1/workbench/tasks'),
    refetchInterval: 30_000,
    retry: false,
  })
  const knowledgeQuery = useQuery({
    queryKey: ['dash-fallback', 'knowledge'],
    queryFn: () =>
      apiClient.get<{ items: unknown[] }>('/platform/v1/knowledge/collections'),
    refetchInterval: 30_000,
    retry: false,
  })

  const events = usageQuery.data?.items ?? []
  const daily = useMemo(() => {
    const groups = new Map<
      string,
      { tokens: number; cost: number; events: number }
    >()
    for (const event of events) {
      const day = new Date(event.occurred_at).toLocaleDateString()
      const current = groups.get(day) ?? { tokens: 0, cost: 0, events: 0 }
      current.tokens +=
        Number(event.input_tokens || 0) + Number(event.output_tokens || 0)
      current.cost += Number(event.cost_usd || 0)
      current.events += 1
      groups.set(day, current)
    }
    return [...groups.entries()].map(([day, value]) => ({ day, ...value }))
  }, [events])
  const maxTokens = Math.max(1, ...daily.map((item) => item.tokens))
  const agents = Object.entries(summaryQuery.data?.by_agent ?? {})
    .sort((a, b) => b[1].runtime_ms - a[1].runtime_ms)
    .slice(0, 8)
  const refreshAll = () => {
    void usageQuery.refetch()
    void summaryQuery.refetch()
    void healthQuery.refetch()
    void sessionsQuery.refetch()
    void agentsQuery.refetch()
    void tasksQuery.refetch()
    void knowledgeQuery.refetch()
  }

  const quotaAvailable = Boolean(
    !usageQuery.isError && usageQuery.data && summaryQuery.data,
  )
  const kpis = [
    {
      to: '/workspace/tasks',
      label: '待处理任务',
      value: number(tasksQuery.data?.items?.length),
      note: '实时同步',
      icon: <ListTodo size={16} strokeWidth={2} />,
      tone: 'blue' as const,
    },
    {
      to: '/admin/agents',
      label: 'Agent 定义',
      value: number(agentsQuery.data?.items?.length),
      note: '在册定义',
      icon: <Bot size={16} strokeWidth={2} />,
      tone: 'violet' as const,
    },
    {
      to: '/operations/knowledge-bases',
      label: '知识库集合',
      value: number(knowledgeQuery.data?.items?.length),
      note: '知识集合',
      icon: <BookOpen size={16} strokeWidth={2} />,
      tone: 'green' as const,
    },
    {
      to: '/operations/conversations',
      label: '活跃会话',
      value: number(sessionsQuery.data?.items?.length),
      note: '实时会话',
      icon: <MessageSquare size={16} strokeWidth={2} />,
      tone: 'amber' as const,
    },
  ]
  const recentEvents = [...events]
    .sort(
      (a, b) =>
        new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime(),
    )
    .slice(0, 4)

  function taskTitle(task: Record<string, unknown>): string {
    return String(
      task.title ?? task.name ?? task.task_id ?? task.id ?? '未命名任务',
    )
  }

  function taskStatus(task: Record<string, unknown>): string {
    return String(task.status ?? 'unknown')
  }

  const sortedDaily = [...daily].sort(
    (a, b) => new Date(a.day).getTime() - new Date(b.day).getTime(),
  )
  const tokenSeries = sortedDaily.map((item) => item.tokens)
  const costSeries = sortedDaily.map((item) => item.cost)
  const allTasks = (tasksQuery.data?.items ?? []).map(
    (item) => item as Record<string, unknown>,
  )
  const isDoneStatus = (status: string) =>
    ['completed', 'succeeded', 'success', 'done'].includes(
      status.toLowerCase(),
    )
  const doneCount = allTasks.filter((task) =>
    isDoneStatus(taskStatus(task)),
  ).length
  const pendingCount = Math.max(0, allTasks.length - doneCount)
  const visibleTasks = allTasks
    .filter((task) =>
      taskTab === 'done'
        ? isDoneStatus(taskStatus(task))
        : !isDoneStatus(taskStatus(task)),
    )
    .slice(0, 4)
  const topAgents = agents.slice(0, 3)
  const hotAgentMax = Math.max(1, ...topAgents.map(([, value]) => value.events))
  const healthStatus = healthQuery.data?.status
  const healthLabel =
    healthStatus === 'ok' || healthStatus === 'healthy'
      ? '运行正常'
      : healthStatus

  return (
    <section className="spatial-home">
      <div className="spatial-top">
        <header className="home-hero">
          <BusinessMark />
          <div className="home-intro">
            <h1>欢迎回来！</h1>
            <p>让 AI 赋能每一个工作场景</p>
          </div>
        </header>
        <div className="kpi-strip">
          {kpis.map((card) => (
            <KpiCard key={card.label} {...card} />
          ))}
        </div>
      </div>

      <div className="spatial-main">
        <div className="spatial-stage" aria-label="3D 办公室快捷入口">
          <Link
            className="stage-hotspot hotspot-create"
            to="/admin/agents"
            title="创建 Agent"
            aria-label="创建 Agent"
          />
          <Link
            className="stage-hotspot hotspot-run"
            to="/workspace/tasks"
            title="运行任务"
            aria-label="运行任务"
          />
          <Link
            className="stage-hotspot hotspot-flow"
            to="/admin/workflows"
            title="管理 Workflow"
            aria-label="管理 Workflow"
          />
        </div>

        <aside className="glass-card workbench-panel">
          <header className="card-heading">
            <h2>我的工作台</h2>
            <span className="card-tag">实时</span>
          </header>
          <div className="wb-tabs" role="tablist" aria-label="任务筛选">
            <button
              type="button"
              role="tab"
              aria-selected={taskTab === 'pending'}
              className={taskTab === 'pending' ? 'active' : ''}
              onClick={() => setTaskTab('pending')}
            >
              待处理 ({pendingCount})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={taskTab === 'done'}
              className={taskTab === 'done' ? 'active' : ''}
              onClick={() => setTaskTab('done')}
            >
              已完成 ({doneCount})
            </button>
          </div>
          {tasksQuery.isLoading ? (
            <p className="muted">正在加载…</p>
          ) : visibleTasks.length ? (
            <ul className="wb-list">
              {visibleTasks.map((task, index) => {
                const status = taskStatus(task)
                return (
                  <li key={String(task.task_id ?? task.id ?? index)}>
                    <span
                      className={`wb-dot status-${status.toLowerCase()}`}
                      aria-hidden="true"
                    />
                    <div className="wb-info">
                      <strong>{taskTitle(task)}</strong>
                      <small>
                        {task.updated_at
                          ? new Date(String(task.updated_at)).toLocaleString()
                          : '更新时间未知'}
                      </small>
                    </div>
                    <span className={`status-pill status-${status.toLowerCase()}`}>
                      {statusLabel(status)}
                    </span>
                  </li>
                )
              })}
            </ul>
          ) : (
            <div className="wb-empty">
              <Inbox size={22} strokeWidth={1.6} aria-hidden="true" />
              <p>暂无{taskTab === 'pending' ? '待处理' : '已完成'}任务</p>
              <small>任务会实时同步到这里</small>
            </div>
          )}
          <Link className="wb-all" to="/workspace/tasks">
            查看全部任务 →
          </Link>
        </aside>
      </div>

      <div className="spatial-bottom">
        <section className="glass-card overview-card">
          <header className="card-heading">
            <h2>系统概览</h2>
            <div className="overview-actions">
              {healthLabel && <span className="status-pill">{healthLabel}</span>}
              <select
                className="mini-select"
                aria-label="时间范围"
                value={days}
                onChange={(event) => setDays(Number(event.target.value))}
              >
                <option value={1}>24 小时</option>
                <option value={7}>7 天</option>
                <option value={30}>30 天</option>
              </select>
              <button
                type="button"
                className="mini-icon"
                onClick={refreshAll}
                aria-label="刷新数据"
              >
                ↻
              </button>
            </div>
          </header>
          {usageQuery.isLoading ? (
            <p className="muted">正在加载…</p>
          ) : usageQuery.isError ? (
            <QueryError
              error={usageQuery.error}
              onRetry={() => void usageQuery.refetch()}
            />
          ) : sortedDaily.length ? (
            <>
              <div className="chart-legend">
                <span>
                  <i className="legend-dot primary" aria-hidden="true" />
                  Token 用量
                </span>
                {quotaAvailable && (
                  <span>
                    <i className="legend-dot secondary" aria-hidden="true" />
                    成本
                  </span>
                )}
                <em>
                  {quotaAvailable
                    ? `${money(summaryQuery.data?.estimated_cost_usd)} · 峰值 ${number(maxTokens)}`
                    : `峰值 ${number(maxTokens)}`}
                </em>
              </div>
              <Sparkline
                primary={tokenSeries}
                secondary={quotaAvailable ? costSeries : []}
              />
              <div className="chart-axis">
                <span>{sortedDaily[0]?.day}</span>
                <span>{sortedDaily[sortedDaily.length - 1]?.day}</span>
              </div>
            </>
          ) : (
            <div className="muted card-empty">
              <ChartLine size={24} strokeWidth={1.5} aria-hidden="true" />
              所选时间范围暂无用量事件
              <small>模型调用产生用量后，这里会展示趋势</small>
            </div>
          )}
        </section>

        <section className="glass-card hot-card">
          <header className="card-heading">
            <h2>热门 Agent</h2>
            <Link to="/operations/agents">更多 →</Link>
          </header>
          {topAgents.length ? (
            <ol className="hot-list">
              {topAgents.map(([agent, value], index) => (
                <li key={agent}>
                  <span className="hot-rank">{index + 1}</span>
                  <span className="hot-avatar" aria-hidden="true">
                    <Bot size={14} strokeWidth={2} />
                  </span>
                  <div className="hot-info">
                    <strong>{agent}</strong>
                    <div className="hot-bar">
                      <span
                        style={{
                          width: `${Math.max(8, (value.events / hotAgentMax) * 100)}%`,
                        }}
                      />
                    </div>
                  </div>
                  <em>{number(value.events)} 次</em>
                </li>
              ))}
            </ol>
          ) : (
            <div className="muted card-empty">
              <Bot size={24} strokeWidth={1.5} aria-hidden="true" />
              暂无 Agent 用量
              <small>接入模型调用后将在此按事件数排序</small>
            </div>
          )}
        </section>

        <section className="glass-card feed-card">
          <header className="card-heading">
            <h2>最近动态</h2>
            <Link to="/operations/conversations">更多 →</Link>
          </header>
          {recentEvents.length ? (
            <ul className="feed-list">
              {recentEvents.slice(0, 3).map((event) => (
                <li key={event.event_id}>
                  <span className="feed-dot" aria-hidden="true" />
                  <time>{new Date(event.occurred_at).toLocaleTimeString()}</time>
                  <span>
                    {event.model_key} · {number(event.total_tokens)} tokens
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="muted card-empty">
              <Activity size={24} strokeWidth={1.5} aria-hidden="true" />
              暂无动态
              <small>任务与用量事件会实时汇总到这里</small>
            </div>
          )}
        </section>
      </div>

      <p className="home-footnote">
        指标来自已接入服务端数据；成功率、并发与失败 TopN 尚未接入，未做估算展示。
      </p>
    </section>
  )
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    running: '运行中',
    queued: '排队中',
    pending: '待处理',
    waiting_approval: '等待审批',
    completed: '已完成',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已取消',
    dead_letter: '死信',
  }
  return labels[status.toLowerCase()] ?? status
}

function Sparkline({
  primary,
  secondary,
}: {
  primary: number[]
  secondary: number[]
}) {
  if (primary.length < 2) {
    return <p className="muted">数据点不足，暂无法绘制趋势。</p>
  }
  const width = 300
  const height = 96
  const pad = 6
  const maxPrimary = Math.max(1, ...primary)
  const maxSecondary = Math.max(0.000001, ...secondary)
  const pathFor = (values: number[], max: number) =>
    values
      .map((value, index) => {
        const x = pad + (index / (values.length - 1)) * (width - pad * 2)
        const y = height - pad - (value / max) * (height - pad * 2)
        return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`
      })
      .join(' ')
  const primaryPath = pathFor(primary, maxPrimary)
  const areaPath = `${primaryPath} L${width - pad} ${height - pad} L${pad} ${height - pad} Z`

  return (
    <svg
      className="sparkline"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Token 与成本趋势"
    >
      <defs>
        <linearGradient id="spark-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#4f8cff" stopOpacity="0.26" />
          <stop offset="100%" stopColor="#4f8cff" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
        <line
          key={ratio}
          className="spark-grid"
          x1={pad}
          x2={width - pad}
          y1={pad + ratio * (height - pad * 2)}
          y2={pad + ratio * (height - pad * 2)}
        />
      ))}
      <path d={areaPath} fill="url(#spark-area)" />
      <path d={primaryPath} className="spark-line primary" />
      {secondary.some((value) => value > 0) && (
        <path
          d={pathFor(secondary, maxSecondary)}
          className="spark-line secondary"
        />
      )}
    </svg>
  )
}
