import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

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
  by_model: Record<string, { events: number; total_tokens: number; cost_usd: string }>
  by_agent: Record<string, { events: number; runtime_ms: number; total_tokens: number; cost_usd: string }>
}

type UsageResponse = { items: UsageEvent[] }
type HealthResponse = { status: string; [key: string]: unknown }
type AlertResponse = { items: Array<{ event_id?: string; title?: string; name?: string; severity?: string; status?: string }> }

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
    ? new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(amount)
    : '—'
}

function number(value: number | undefined): string {
  return value === undefined ? '—' : new Intl.NumberFormat().format(value)
}

function requestId(error: unknown): string | undefined {
  return error instanceof ApiError ? error.requestId : undefined
}

function MetricCard({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <article className="ops-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  )
}

function MissingPanel({ title, reason }: { title: string; reason: string }) {
  return (
    <section className="ops-panel ops-unavailable">
      <div className="ops-panel-heading"><h2>{title}</h2><span className="ops-tag">API 缺口</span></div>
      <p>{reason}</p>
    </section>
  )
}

function QueryError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const id = requestId(error)
  return (
    <div className="ops-query-error" role="alert">
      <span>{error instanceof Error ? error.message : '数据读取失败'}</span>
      {id && <code>request_id: {id}</code>}
      <button type="button" onClick={onRetry}>重试</button>
    </div>
  )
}

export function Dashboard() {
  const [days, setDays] = useState(7)
  const [modelKey, setModelKey] = useState('')
  const query = queryRange(days, modelKey || undefined)
  const usageQuery = useQuery({
    queryKey: ['operations-dashboard', 'usage', query],
    queryFn: () => apiClient.get<UsageResponse>(`/platform/v1/quotas/usage?${query}`),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  const summaryQuery = useQuery({
    queryKey: ['operations-dashboard', 'summary', query],
    queryFn: () => apiClient.get<UsageSummary>(`/platform/v1/quotas/usage/summary?${query}`),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  const healthQuery = useQuery({
    queryKey: ['operations-dashboard', 'capacity'],
    queryFn: () => apiClient.get<HealthResponse>('/platform/v1/observability/health/capacity'),
    refetchInterval: 30_000,
    retry: false,
  })
  const alertsQuery = useQuery({
    queryKey: ['operations-dashboard', 'alerts'],
    queryFn: () => apiClient.get<AlertResponse>('/platform/v1/observability/alerts?limit=100'),
    refetchInterval: 30_000,
    retry: false,
  })

  const events = usageQuery.data?.items ?? []
  const daily = useMemo(() => {
    const groups = new Map<string, { tokens: number; cost: number; events: number }>()
    for (const event of events) {
      const day = new Date(event.occurred_at).toLocaleDateString()
      const current = groups.get(day) ?? { tokens: 0, cost: 0, events: 0 }
      current.tokens += Number(event.input_tokens || 0) + Number(event.output_tokens || 0)
      current.cost += Number(event.cost_usd || 0)
      current.events += 1
      groups.set(day, current)
    }
    return [...groups.entries()].map(([day, value]) => ({ day, ...value }))
  }, [events])
  const maxTokens = Math.max(1, ...daily.map((item) => item.tokens))
  const maxCost = Math.max(0.000001, ...daily.map((item) => item.cost))
  const agents = Object.entries(summaryQuery.data?.by_agent ?? {})
    .sort((a, b) => b[1].runtime_ms - a[1].runtime_ms)
    .slice(0, 8)
  const models = Object.keys(summaryQuery.data?.by_model ?? {}).sort()

  const refreshAll = () => {
    void usageQuery.refetch()
    void summaryQuery.refetch()
    void healthQuery.refetch()
    void alertsQuery.refetch()
  }

  return (
    <section className="page-content dashboard-page operations-dashboard">
      <div className="ops-dashboard-header">
        <div>
          <p className="eyebrow">AGENT PLATFORM / OPERATIONS</p>
          <h1>运营驾驶舱</h1>
          <p>展示已接入服务端数据；未提供的指标明确标注 API 缺口。</p>
        </div>
        <div className="ops-dashboard-controls">
          <label>时间范围
            <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
              <option value={1}>近 24 小时</option>
              <option value={7}>近 7 天</option>
              <option value={30}>近 30 天</option>
            </select>
          </label>
          <label>模型
            <select value={modelKey} onChange={(event) => setModelKey(event.target.value)}>
              <option value="">全部模型</option>
              {models.map((model) => <option value={model} key={model}>{model}</option>)}
            </select>
          </label>
          <button type="button" onClick={refreshAll}>刷新数据</button>
        </div>
      </div>

      {(usageQuery.isError || summaryQuery.isError) && (
        <QueryError error={usageQuery.error ?? summaryQuery.error} onRetry={refreshAll} />
      )}
      <div className="ops-metric-grid">
        <MetricCard label="用量事件" value={number(summaryQuery.data?.events)} note="UsageEvent 记录数，不等同于任务数" />
        <MetricCard label="Token 总量" value={number(summaryQuery.data?.total_tokens)} note="input_tokens + output_tokens" />
        <MetricCard label="估算成本" value={money(summaryQuery.data?.estimated_cost_usd)} note="source=estimated，USD" />
        <MetricCard label="Provider 实报成本" value={money(summaryQuery.data?.provider_reported_cost_usd)} note="source=provider_reported，USD" />
      </div>

      <div className="ops-dashboard-grid">
        <section className="ops-panel">
          <div className="ops-panel-heading"><h2>Token 用量趋势</h2><span>按事件发生日期聚合</span></div>
          {usageQuery.isLoading ? <p className="muted">正在加载…</p> : daily.length ? (
            <div className="ops-bars" aria-label="每日 Token 用量">
              {daily.map((item) => (
                <div className="ops-bar-column" key={item.day}>
                  <strong>{number(item.tokens)}</strong>
                  <div className="ops-bar-track"><span style={{ height: `${Math.max(3, item.tokens / maxTokens * 100)}%` }} /></div>
                  <small>{item.day}</small>
                </div>
              ))}
            </div>
          ) : <p className="muted">所选时间范围暂无用量事件。</p>}
        </section>
        <section className="ops-panel">
          <div className="ops-panel-heading"><h2>成本趋势</h2><span>UsageEvent.cost_usd，USD</span></div>
          {usageQuery.isLoading ? <p className="muted">正在加载…</p> : daily.length ? (
            <div className="ops-bars" aria-label="每日成本">
              {daily.map((item) => (
                <div className="ops-bar-column" key={item.day}>
                  <strong>{money(String(item.cost))}</strong>
                  <div className="ops-bar-track"><span style={{ height: `${Math.max(3, item.cost / maxCost * 100)}%` }} /></div>
                  <small>{item.day}</small>
                </div>
              ))}
            </div>
          ) : <p className="muted">所选时间范围暂无成本事件。</p>}
        </section>
        <section className="ops-panel">
          <div className="ops-panel-heading"><h2>Agent 活动</h2><span>按累计 runtime_ms 排序，非并发负载</span></div>
          {summaryQuery.isLoading ? <p className="muted">正在加载…</p> : agents.length ? (
            <ol className="ops-ranking">
              {agents.map(([agent, value]) => (
                <li key={agent}><span>{agent}</span><strong>{number(value.runtime_ms)} ms</strong><small>{number(value.events)} 个用量事件</small></li>
              ))}
            </ol>
          ) : <p className="muted">暂无 Agent 归因用量。</p>}
        </section>
        <section className="ops-panel">
          <div className="ops-panel-heading"><h2>模型分布</h2><span>按真实用量事件聚合</span></div>
          {summaryQuery.isLoading ? <p className="muted">正在加载…</p> : models.length ? (
            <ol className="ops-ranking">
              {Object.entries(summaryQuery.data?.by_model ?? {}).sort((a, b) => b[1].total_tokens - a[1].total_tokens).slice(0, 8).map(([model, value]) => (
                <li key={model}><span>{model}</span><strong>{number(value.total_tokens)} tokens</strong><small>{money(value.cost_usd)}</small></li>
              ))}
            </ol>
          ) : <p className="muted">暂无模型用量。</p>}
        </section>
      </div>

      <div className="ops-dashboard-grid">
        <MissingPanel title="任务吞吐与成功率" reason="当前核实到的任务列表 API 不提供稳定的全局时间序列、成功率分子/分母与去重口径；需新增服务端 dashboard metrics 聚合 API。" />
        <MissingPanel title="Agent 实时负载" reason="usage summary 的 runtime_ms 是累计运行时长，不是并发数、队列长度或心跳在线状态；需独立 Agent runtime/queue metrics API。" />
        <MissingPanel title="失败原因 TopN" reason="现有 dead-letter/task 资源不是统一的失败原因聚合接口；缺少标准化 failure_code、计数窗口和去重口径。" />
        <MissingPanel title="任务状态分布 / 待审批 KPI" reason="尚未核实可跨项目聚合且具备稳定状态枚举的只读统计 API；不从局部列表推算全局数量。" />
      </div>

      <div className="ops-dashboard-grid">
        <section className="ops-panel">
          <div className="ops-panel-heading"><h2>容量健康</h2><span>observability/health/capacity</span></div>
          {healthQuery.isLoading ? <p className="muted">正在加载…</p> : healthQuery.isError ? (
            <QueryError error={healthQuery.error} onRetry={() => void healthQuery.refetch()} />
          ) : <p className="ops-health-status">{healthQuery.data?.status ?? '未知'}</p>}
        </section>
        <section className="ops-panel">
          <div className="ops-panel-heading"><h2>未关闭告警</h2><span>observability/alerts</span></div>
          {alertsQuery.isLoading ? <p className="muted">正在加载…</p> : alertsQuery.isError ? (
            <QueryError error={alertsQuery.error} onRetry={() => void alertsQuery.refetch()} />
          ) : <strong className="ops-alert-count">{number(alertsQuery.data?.items?.length)}</strong>}
        </section>
      </div>
      <p className="ops-updated-at">数据自动刷新：用量 60 秒；健康与告警 30 秒。成本仅展示服务端 ledger 记录，不混合估算与 Provider 实报。</p>
    </section>
  )
}
