import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { Activity, AlertTriangle, Bot, Cpu, Gauge, RefreshCw, Wallet } from 'lucide-react'

import { ApiError, apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import './operations-cockpit-extension.css'
import { buildTaskDateDrilldown } from './operations-cockpit-metrics'

type UsageEvent = {
  event_id: string
  run_id: string
  model_key: string
  project_id?: string | null
  agent_id?: string | null
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: string
  occurred_at: string
  source?: string
}
type TaskRecord = Record<string, unknown>
type AlertRecord = {
  event_id: string
  description?: string
  severity?: string
  status?: string
  fired_at?: string
  metric_name?: string
}
type HealthRecord = { status?: string; [key: string]: unknown }
type UsageSummary = {
  estimated_cost_usd?: string
  provider_reported_cost_usd?: string
  reconciled_event_cost_usd?: string
}

const DAY_OPTIONS = [1, 7, 30, 90]
const statusLabel: Record<string, string> = {
  queued: '排队中', pending: '待处理', running: '运行中',
  completed: '已完成', succeeded: '已完成', success: '已完成',
  failed: '失败', error: '失败', cancelled: '已取消',
  waiting_approval: '待审批', awaiting_approval: '待审批',
  pending_approval: '待审批', approval_pending: '待审批',
}

function localRange(days: number) {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - days)
  return { start: start.toISOString(), end: end.toISOString() }
}

function readItems<T>(value: unknown): T[] {
  if (!value || typeof value !== 'object') return []
  const items = (value as { items?: unknown }).items
  return Array.isArray(items) ? items as T[] : []
}

export function filterUsageEvents(
  events: UsageEvent[],
  project: string,
  agent: string,
  model: string,
) {
  return events.filter((event) =>
    (!project || event.project_id === project) &&
    (!agent || event.agent_id === agent) &&
    (!model || event.model_key === model),
  )
}

function queryErrorId(error: unknown) {
  return error instanceof ApiError ? error.requestId : undefined
}

function errorDescription(error: unknown) {
  const message = error instanceof Error ? error.message : '请求失败'
  const id = queryErrorId(error)
  return id ? `${message} · request_id: ${id}` : message
}

function queryString(params: URLSearchParams) {
  const copy = new URLSearchParams(params)
  copy.sort()
  return copy.toString()
}

export function OperationsCockpitExtension() {
  const [searchParams, setSearchParams] = useSearchParams()
  const days = DAY_OPTIONS.includes(Number(searchParams.get('days')))
    ? Number(searchParams.get('days')) : 7
  const project = searchParams.get('project') ?? ''
  const agent = searchParams.get('agent') ?? ''
  const model = searchParams.get('model') ?? ''
  const range = useMemo(() => localRange(days), [days])
  const serverParams = new URLSearchParams({ start: range.start, end: range.end })
  if (model) serverParams.set('model_key', model)

  const usageQuery = useQuery({
    queryKey: ['operations-cockpit-extension', 'usage', queryString(serverParams)],
    queryFn: () => apiClient.get<{ items: UsageEvent[] }>(`/platform/v1/quotas/usage?${serverParams}`),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  })
  const summaryQuery = useQuery({
    queryKey: ['operations-cockpit-extension', 'usage-summary', queryString(serverParams)],
    queryFn: () => apiClient.get<UsageSummary>(`/platform/v1/quotas/usage/summary?${serverParams}`),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  })
  const taskQuery = useQuery({
    queryKey: ['operations-cockpit-extension', 'tasks'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/workbench/tasks'),
    refetchInterval: 30_000,
    retry: false,
  })
  const alertsQuery = useQuery({
    queryKey: ['operations-cockpit-extension', 'alerts'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/observability/alerts?limit=100'),
    refetchInterval: 30_000,
    retry: false,
  })
  const healthQuery = useQuery({
    queryKey: ['operations-cockpit-extension', 'capacity-health'],
    queryFn: () => apiClient.get<HealthRecord>('/platform/v1/observability/health/capacity'),
    refetchInterval: 30_000,
    retry: false,
  })

  const rawEvents = usageQuery.data?.items ?? []
  const events = useMemo(
    () => filterUsageEvents(rawEvents, project, agent, model),
    [rawEvents, project, agent, model],
  )
  const tasks = readItems<TaskRecord>(taskQuery.data)
  const alerts = readItems<AlertRecord>(alertsQuery.data)
  const openAlerts = alerts.filter((alert) => alert.status === 'open' || alert.status === 'acknowledged')
  const projects = [...new Set(rawEvents.map((event) => event.project_id).filter((value): value is string => Boolean(value)))].sort()
  const agents = [...new Set(rawEvents.map((event) => event.agent_id).filter((value): value is string => Boolean(value)))].sort()
  const models = [...new Set(rawEvents.map((event) => event.model_key).filter(Boolean))].sort()
  const taskStatuses = tasks.reduce<Record<string, number>>((result, task) => {
    const status = String(task.status ?? 'unknown').toLowerCase()
    result[status] = (result[status] ?? 0) + 1
    return result
  }, {})
  const agentUsage = events.reduce<Record<string, number>>((result, event) => {
    if (event.agent_id) result[event.agent_id] = (result[event.agent_id] ?? 0) + 1
    return result
  }, {})
  const modelUsage = events.reduce<Record<string, number>>((result, event) => {
    result[event.model_key] = (result[event.model_key] ?? 0) + 1
    return result
  }, {})
  const daily = events.reduce<Record<string, number>>((result, event) => {
    const date = new Date(event.occurred_at)
    if (!Number.isNaN(date.getTime())) {
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
      result[key] = (result[key] ?? 0) + Number(event.total_tokens ?? 0)
    }
    return result
  }, {})
  const dailyRows = Object.entries(daily).sort(([a], [b]) => a.localeCompare(b))
  const maxDaily = Math.max(1, ...dailyRows.map(([, value]) => value))
  const healthStatus = healthQuery.data?.status
  const updatedAt = usageQuery.dataUpdatedAt ? new Date(usageQuery.dataUpdatedAt).toLocaleString() : '尚未更新'
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next, { replace: true })
  }
  const refresh = () => {
    void usageQuery.refetch()
    void summaryQuery.refetch()
    void taskQuery.refetch()
    void alertsQuery.refetch()
    void healthQuery.refetch()
  }
  const filteredQuery = new URLSearchParams({ days: String(days) })
  if (project) filteredQuery.set('project', project)
  if (agent) filteredQuery.set('agent', agent)
  if (model) filteredQuery.set('model', model)
  const taskDrill = `/workspace/tasks?${filteredQuery.toString()}`
  const formatMoney = (value?: string) => {
    if (value === undefined || value === null || value.trim() === '') return '—'
    const amount = Number(value)
    return Number.isFinite(amount)
      ? new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(amount)
      : '—'
  }

  return (
    <section className="page-content ops-cockpit-extension" aria-labelledby="ops-cockpit-title">
      <header className="ops-cockpit-heading">
        <div>
          <p className="eyebrow">运营分析 / 实时态势</p>
          <h2 id="ops-cockpit-title">运营驾驶舱</h2>
          <p>筛选当前授权范围内的用量与任务样本；聚合指标仅展示已由服务端 API 证实的数据。</p>
        </div>
        <div className="ops-cockpit-refresh">
          <span>用量更新时间：{updatedAt}{usageQuery.isStale ? ' · 数据可能过期' : ''}</span>
          <button type="button" className="button-secondary" onClick={refresh}><RefreshCw size={14} /> 刷新</button>
        </div>
      </header>

      <div className="ops-cockpit-filters" aria-label="驾驶舱筛选条件">
        <label>时间范围
          <select value={String(days)} onChange={(event) => setFilter('days', event.target.value)}>
            {DAY_OPTIONS.map((value) => <option key={value} value={value}>{value === 1 ? '近 24 小时' : `近 ${value} 天`}</option>)}
          </select>
        </label>
        <label>项目
          <select value={project} onChange={(event) => setFilter('project', event.target.value)}>
            <option value="">全部项目（已返回事件）</option>
            {projects.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>Agent
          <select value={agent} onChange={(event) => setFilter('agent', event.target.value)}>
            <option value="">全部 Agent（已返回事件）</option>
            {agents.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>模型
          <select value={model} onChange={(event) => setFilter('model', event.target.value)}>
            <option value="">全部模型</option>
            {models.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
      </div>
      <p className="ops-cockpit-filter-note">项目与 Agent 条件在已授权返回的用量事件集合内二次筛选；当前 API 尚未提供这两个维度的服务端过滤参数。切勿将此筛选视为权限边界。</p>

      <div className="ops-cockpit-grid">
        <article className="ops-cockpit-panel ops-cockpit-wide">
          <header><h3><Activity size={17} /> Token 吞吐趋势</h3><span>{events.length ? `${events.length} 条用量事件` : '暂无数据'}</span></header>
          {usageQuery.isPending ? <LoadingState label="正在加载用量趋势" /> : usageQuery.isError ? <ErrorState title="用量趋势加载失败" description={`${usageQuery.error instanceof Error ? usageQuery.error.message : '请求失败'}${queryErrorId(usageQuery.error) ? ` · request_id: ${queryErrorId(usageQuery.error)}` : ''}`} onRetry={() => void usageQuery.refetch()} /> : dailyRows.length ? (
            <div className="ops-cockpit-bars" aria-label="按本地日期聚合的 Token 用量趋势">
              {dailyRows.map(([day, value]) => <Link key={day} to={buildTaskDateDrilldown(day)} title={`${day}：${value.toLocaleString()} tokens`}><span style={{ height: `${Math.max(5, value / maxDaily * 100)}%` }} /><small>{day}</small></Link>)}
            </div>
          ) : <EmptyState title="所选范围暂无用量事件" description="无数据不以 0 替代。" />}
        </article>

        <article className="ops-cockpit-panel">
          <header><h3><Wallet size={17} /> 成本口径</h3><Link to="/workspace/quality-cost">成本与质量 →</Link></header>
          {summaryQuery.isPending ? <LoadingState label="正在加载成本汇总" /> : summaryQuery.isError ? <ErrorState title="成本汇总不可用" description={errorDescription(summaryQuery.error)} onRetry={() => void summaryQuery.refetch()} /> : summaryQuery.data ? (
            <dl className="ops-cost-breakdown">
              <div><dt>估算成本</dt><dd>{formatMoney(summaryQuery.data.estimated_cost_usd)}</dd></div>
              <div><dt>供应商报告</dt><dd>{formatMoney(summaryQuery.data.provider_reported_cost_usd)}</dd></div>
              <div><dt>已核对事件成本</dt><dd>{formatMoney(summaryQuery.data.reconciled_event_cost_usd)}</dd></div>
            </dl>
          ) : <EmptyState title="暂无成本汇总" description="服务端未返回成本口径数据，不以 0 替代。" />}
          <p className="ops-api-gap">三种成本口径分开展示，不相加、不互相替代；当前没有经核实的日级成本序列 API。</p>
        </article>

        <article className="ops-cockpit-panel">
          <header><h3><AlertTriangle size={17} /> 风险与告警</h3><Link to="/operations/alerts">告警中心 →</Link></header>
          {alertsQuery.isPending ? <LoadingState label="正在加载告警" /> : alertsQuery.isError ? <ErrorState title="告警加载失败" description={errorDescription(alertsQuery.error)} onRetry={() => void alertsQuery.refetch()} /> : openAlerts.length ? (
            <ul className="ops-cockpit-list">{openAlerts.slice(0, 5).map((alert) => <li key={alert.event_id}><span className={`ops-risk-dot severity-${alert.severity ?? 'unknown'}`} /><div><strong>{alert.description || alert.metric_name || '告警事件'}</strong><small>{alert.fired_at ? new Date(alert.fired_at).toLocaleString() : '发生时间未知'}</small></div><Link to="/operations/alerts">查看</Link></li>)}</ul>
          ) : <EmptyState title="暂无未关闭告警" description="当前 API 返回集合中没有 open/acknowledged 告警。" />}
        </article>

        <article className="ops-cockpit-panel">
          <header><h3><Gauge size={17} /> 组件健康</h3><Link to="/admin/operations">运维诊断 →</Link></header>
          {healthQuery.isPending ? <LoadingState label="正在检查组件健康" /> : healthQuery.isError ? <ErrorState title="组件健康不可用" description={errorDescription(healthQuery.error)} onRetry={() => void healthQuery.refetch()} /> : (
            <div className="ops-health-summary"><span className={`ops-health-dot state-${healthStatus ?? 'unknown'}`} /><strong>{healthStatus ?? '未知状态'}</strong><small>来源：/observability/health/capacity</small></div>
          )}
          <p className="ops-api-gap">队列深度、worker 分项健康和逐组件状态尚无统一已注册契约；不以单一 capacity 状态推断。</p>
        </article>

        <article className="ops-cockpit-panel">
          <header><h3><Bot size={17} /> Agent 用量负载</h3><Link to="/operations/agents">Agent 运行态 →</Link></header>
          {Object.keys(agentUsage).length ? <ol className="ops-cockpit-rank">{Object.entries(agentUsage).sort((a,b) => b[1]-a[1]).slice(0,5).map(([id,count]) => <li key={id}><Link to={`/operations/agents/${encodeURIComponent(id)}`}>{id}</Link><span>{count} 条用量事件</span></li>)}</ol> : <EmptyState title="暂无 Agent 用量" description="Agent 实时并发、enabled/available 与心跳状态需要独立运行态 API。" />}
        </article>

        <article className="ops-cockpit-panel">
          <header><h3><Cpu size={17} /> 模型分布</h3><span>{Object.keys(modelUsage).length} 个模型</span></header>
          {Object.keys(modelUsage).length ? <ol className="ops-cockpit-rank">{Object.entries(modelUsage).sort((a,b) => b[1]-a[1]).slice(0,5).map(([id,count]) => <li key={id}><button type="button" onClick={() => setFilter('model', id)}>{id}</button><span>{count} 条事件</span></li>)}</ol> : <EmptyState title="暂无模型用量" description="所选时间范围没有模型用量事件。" />}
        </article>

        <article className="ops-cockpit-panel">
          <header><h3>任务状态分布</h3><Link to={taskDrill}>任务明细 →</Link></header>
          {taskQuery.isPending ? <LoadingState label="正在加载任务样本" /> : taskQuery.isError ? <ErrorState title="任务列表加载失败" description={errorDescription(taskQuery.error)} onRetry={() => void taskQuery.refetch()} /> : Object.keys(taskStatuses).length ? <ul className="ops-task-distribution">{Object.entries(taskStatuses).sort((a,b) => b[1]-a[1]).map(([status,count]) => <li key={status}><Link to={`/workspace/tasks?status=${encodeURIComponent(status)}`}>{statusLabel[status] ?? status}</Link><strong>{count}</strong></li>)}</ul> : <EmptyState title="暂无任务样本" description="服务端没有返回任务记录。" />}
          <p className="ops-api-gap">分布仅统计当前 workbench/tasks 响应样本，不代表全局队列或全量任务聚合。</p>
        </article>
      </div>

      <div className="ops-api-gaps" role="note">
        <strong>尚未由 API 证实的运营指标</strong>
        <span>任务吞吐/成功率时间序列、失败原因 TopN、全局待审批计数、queue_depth、Agent 在线/可用/并发、P50/P95、TTFT、逐组件健康：当前不显示推算值。</span>
        <Link to="/workspace/tasks">查看任务明细 →</Link>
      </div>
    </section>
  )
}
