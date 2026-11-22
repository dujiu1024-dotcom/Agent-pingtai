import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { Pagination } from '@/components/Pagination'
import { ApiError, apiClient } from '@/lib/api'
import {
  displayValue,
  fieldLabel,
  formatDateTime,
  localizedJson,
  valueLabel,
} from '@/lib/i18n/labels'
import { buildDailyUsageTrend } from './usage-trends'
import './cost-quality-center.css'

type UsageEvent = {
  event_id: string
  run_id?: string
  model_key: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cached_tokens?: number | null
  reasoning_tokens?: number | null
  currency?: string
  cost_basis?: string
  pricing_version?: string
  estimate_or_settled?: string
  first_token_ms?: number | null
  latency_ms?: number | null
  status?: string
  error_code?: string
  cost_usd: string
  project_id?: string | null
  agent_id?: string | null
  runtime_ms: number
  source: string
  occurred_at: string
}
type UsageSummary = {
  events: number
  request_count?: number
  success_rate?: number
  error_rate?: number
  p50_latency_ms?: number
  p95_latency_ms?: number
  first_token_ms_p50?: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  tool_calls: number
  runtime_ms: number
  storage_bytes: number
  estimated_cost_usd: string
  provider_reported_cost_usd: string
  reconciled_event_cost_usd: string
  estimated_vs_provider_actual_variance_usd: string
  by_model: Record<string, { events: number; total_tokens?: number; cost_usd: string }>
  by_project: Record<string, { events: number; cost_usd: string }>
  by_agent?: Record<string, { events: number; cost_usd: string }>
  price_card_versions: string[]
  tool_and_storage_pricing: string
}
type ListResponse<T> = { items?: T[]; data?: T[]; count?: number }
type Health = { status: 'ok' | 'warning' | 'critical' | 'unavailable'; [key: string]: unknown }
type EvaluationReport = Record<string, unknown>
type EffectivePolicy = {
  tenant_id: string
  limits: Record<string, number>
  sources: Record<string, string>
  policy_versions: Record<string, number>
  resolved_at: string
}
type AdminSettingsResponse = {
  items?: { section: string; rows: Record<string, unknown>[]; count?: number }[]
}
type ReconciliationResult = Record<string, unknown>

const DIMENSION_LABELS: Record<string, string> = {
  tokens: 'Token',
  input_tokens: '输入 Token',
  output_tokens: '输出 Token',
  cost_usd: '费用（美元）',
  concurrency: '并发',
  storage_bytes: '存储（字节）',
  requests: '请求数',
  tool_calls: '工具调用',
  runtime_ms: '运行时长（毫秒）',
}

const dateInput = (date: Date) => {
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}
const defaultStart = () => {
  const date = new Date()
  date.setDate(date.getDate() - 29)
  return dateInput(date)
}
const today = () => dateInput(new Date())
const isoStart = (value: string) => new Date(value + 'T00:00:00').toISOString()
const isoEnd = (value: string) => new Date(value + 'T23:59:59.999').toISOString()
const params = (start: string, end: string, model?: string, runId?: string, projectId?: string, agentId?: string) => {
  const search = new URLSearchParams({ start: isoStart(start), end: isoEnd(end) })
  if (model) search.set('model_key', model)
  if (runId) search.set('run_id', runId)
  if (projectId) search.set('project_id', projectId)
  if (agentId) search.set('agent_id', agentId)
  return search.toString()
}
const number = (value?: number | null) =>
  value == null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat().format(value)
const usd = (value?: string) => {
  if (value == null || value === '') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 6 }).format(parsed)
    : '—'
}
const errorText = (error: unknown) => error instanceof Error ? error.message : '请求失败'
const quotaErrorText = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 401) return '登录状态已失效，请重新登录。'
    if (error.status === 403) return '当前账号没有配额管理权限，无法读取策略、对账或导出用量。'
    if (error.status === 422) return '请求参数不符合服务端要求，请检查时间范围、来源与金额格式。'
    if (error.status === 503) return '服务端未启用配额策略、对账或用量导出能力。'
    if (error.status === 400) return '配额请求参数不合法，请检查周期、来源与金额后重试。'
    if (error.status === 404) return '服务端没有找到对应的配额记录或接口。'
  }
  return errorText(error)
}
const limitText = (value: unknown) => {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(parsed)
    : '—'
}
const moneyText = (value: unknown) =>
  typeof value === 'string' || typeof value === 'number' ? usd(String(value)) : '—'
const unwrapItems = <T,>(value: ListResponse<T> | T[]): T[] =>
  Array.isArray(value) ? value : Array.isArray(value.items) ? value.items : Array.isArray(value.data) ? value.data : []

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <article className="cq-metric"><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</article>
}

function TrendLine({ label, values, format }: { label: string; values: Array<number | null>; format: (value: number) => string }) {
  const finite = values.filter((value): value is number => value != null && Number.isFinite(value))
  if (!finite.length) return <div className="cq-trend-unavailable">当前范围没有可绘制的{label}数据</div>
  const min = Math.min(...finite)
  const max = Math.max(...finite)
  const span = max - min || 1
  const points = values.map((value, index) => value == null || !Number.isFinite(value)
    ? null
    : `${values.length < 2 ? 50 : index / (values.length - 1) * 100},${40 - ((value - min) / span) * 32}`)
  const segments: string[][] = []
  points.forEach((point) => {
    if (point == null) return
    if (!segments.length || points[points.indexOf(point) - 1] == null) segments.push([])
    segments[segments.length - 1].push(point)
  })
  return <div className="cq-trend-chart">
    <div className="cq-trend-summary"><span>{label}</span><strong>{format(finite.reduce((sum, value) => sum + value, 0))}</strong></div>
    <svg viewBox="0 0 100 44" preserveAspectRatio="none" role="img" aria-label={`${label}每日趋势，${finite.length}个有值日期`}>
      <line x1="0" y1="40" x2="100" y2="40" />
      {segments.filter((segment) => segment.length > 1).map((segment, index) => <polyline key={index} points={segment.join(' ')} />)}
      {points.map((point, index) => point == null ? null : <circle key={index} cx={point.split(',')[0]} cy={point.split(',')[1]} r="1.4" />)}
    </svg>
    <div className="cq-trend-range"><span>按 UTC 日期</span><span>{finite.length} 个数据点</span></div>
  </div>
}

export function CostQualityCenter() {
  const [start, setStart] = useState(defaultStart)
  const [end, setEnd] = useState(today)
  const [model, setModel] = useState('')
  const [runId, setRunId] = useState('')
  const [projectId, setProjectId] = useState('')
  const [agentId, setAgentId] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [submittedDataset, setSubmittedDataset] = useState('')
  const [tab, setTab] = useState<'cost' | 'quality' | 'quota'>('cost')
  const [policyProjectId, setPolicyProjectId] = useState('')
  const [policySubjectId, setPolicySubjectId] = useState('')
  const [submittedPolicy, setSubmittedPolicy] = useState({ project: '', subject: '' })
  const [reconcileStart, setReconcileStart] = useState(defaultStart)
  const [reconcileEnd, setReconcileEnd] = useState(today)
  const [reconcileSource, setReconcileSource] = useState('')
  const [reconcileCost, setReconcileCost] = useState('')
  const [reconcileError, setReconcileError] = useState('')
  const [usagePage, setUsagePage] = useState(1)
  const [usagePageSize, setUsagePageSize] = useState(20)

  // 时间范围/模型变化时重置页码
  useEffect(() => { setUsagePage(1) }, [start, end, model, runId, projectId, agentId])

  const rangeValid = Boolean(start && end && start <= end)
  const query = params(start, end, model || undefined, runId || undefined, projectId || undefined, agentId || undefined)
  const summary = useQuery({
    queryKey: ['quality-cost', 'summary', start, end, model, runId, projectId, agentId],
    queryFn: () => apiClient.get<UsageSummary>(`/platform/v1/quotas/usage/summary?${query}`),
    enabled: rangeValid,
    refetchInterval: 30_000,
  })
  const usage = useQuery({
    queryKey: ['quality-cost', 'usage', start, end, model, runId, projectId, agentId],
    queryFn: () => apiClient.get<ListResponse<UsageEvent>>(`/platform/v1/quotas/usage?${query}&limit=1000`),
    enabled: rangeValid,
    refetchInterval: 30_000,
  })
  const health = useQuery({
    queryKey: ['quality-cost', 'capacity-health'],
    queryFn: () => apiClient.get<Health>('/platform/v1/observability/health/capacity'),
    refetchInterval: 30_000,
    retry: false,
  })
  const reports = useQuery({
    queryKey: ['quality-cost', 'evaluation-reports', submittedDataset],
    queryFn: () => apiClient.get<ListResponse<EvaluationReport>>(
      `/platform/v1/observability/evaluations?${new URLSearchParams({ dataset_id: submittedDataset, limit: '20' })}`,
    ),
    enabled: Boolean(submittedDataset),
    retry: false,
  })
  const effectivePolicy = useQuery({
    queryKey: ['quality-cost', 'quota-policy', submittedPolicy.project, submittedPolicy.subject],
    queryFn: () => {
      const search = new URLSearchParams()
      if (submittedPolicy.project) search.set('project_id', submittedPolicy.project)
      if (submittedPolicy.subject) search.set('subject_id', submittedPolicy.subject)
      const suffix = search.toString()
      return apiClient.get<EffectivePolicy>(
        `/platform/v1/quotas/policies/effective${suffix ? `?${suffix}` : ''}`,
      )
    },
    enabled: tab === 'quota',
    retry: false,
  })
  const adminSettings = useQuery({
    queryKey: ['quality-cost', 'quota-budgets'],
    queryFn: () => apiClient.get<AdminSettingsResponse>('/platform/v1/admin/settings'),
    enabled: tab === 'quota',
    retry: false,
  })
  const reconcile = useMutation({
    mutationFn: (payload: {
      period_start: string
      period_end: string
      source: string
      actual_cost_usd: string
    }) =>
      apiClient.post<ReconciliationResult>(
        '/platform/v1/quotas/reconciliation',
        payload,
      ),
    onMutate: () => setReconcileError(''),
    onSuccess: () => setReconcileError(''),
    onError: (error) => setReconcileError(quotaErrorText(error)),
  })
  const downloadCsv = useMutation({
    mutationFn: async () => {
      const search = new URLSearchParams({
        start: isoStart(start),
        end: isoEnd(end),
        limit: '10000',
      })
      if (model) search.set('model_key', model)
      if (runId) search.set('run_id', runId)
      if (projectId) search.set('project_id', projectId)
      if (agentId) search.set('agent_id', agentId)
      // 与 apiClient 相同：同源 Cookie 会话随请求发送。
      const response = await fetch(`/api/platform/v1/quotas/usage.csv?${search.toString()}`, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { Accept: 'text/csv' },
      }).catch(() => {
        throw new ApiError('无法连接服务端，请稍后重试。', {
          status: 0,
          code: 'NETWORK_ERROR',
        })
      })
      if (!response.ok) {
        let detail = ''
        try {
          const payload = (await response.json()) as { message?: string; detail?: string }
          detail = payload.message ?? (typeof payload.detail === 'string' ? payload.detail : '')
        } catch {
          detail = ''
        }
        throw new ApiError(detail || '用量 CSV 导出失败。', {
          status: response.status,
          code: 'CSV_EXPORT_FAILED',
        })
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `usage-${start}-${end}.csv`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      return blob.size
    },
  })

  const events = useMemo(() => unwrapItems(usage.data ?? []), [usage.data])
  const dailyTrend = useMemo(() => buildDailyUsageTrend(events), [events])
  const budgetRows = useMemo(
    () =>
      adminSettings.data?.items?.find((item) => item.section === 'quota_budgets')?.rows ?? [],
    [adminSettings.data],
  )
  const modelOptions = useMemo(
    () => [...new Set(events.map((event) => event.model_key).filter(Boolean))].sort(),
    [events],
  )
  const byModel = Object.entries(summary.data?.by_model ?? {}).sort(
    (a, b) => Number(b[1].cost_usd) - Number(a[1].cost_usd),
  )
  const maxModelCost = Math.max(0, ...byModel.map(([, item]) => Number(item.cost_usd) || 0))
  const reportItems = unwrapItems(reports.data ?? [])

  const submitPolicy = (event: FormEvent) => {
    event.preventDefault()
    setSubmittedPolicy({
      project: policyProjectId.trim(),
      subject: policySubjectId.trim(),
    })
  }

  const submitReconcile = (event: FormEvent) => {
    event.preventDefault()
    setReconcileError('')
    if (!reconcileStart || !reconcileEnd || reconcileStart >= reconcileEnd) {
      setReconcileError('请填写有效周期，开始日期需早于结束日期。')
      return
    }
    const source = reconcileSource.trim()
    if (!source) {
      setReconcileError('请填写对账来源标签。')
      return
    }
    if (source.length > 128) {
      setReconcileError('对账来源标签不能超过 128 个字符。')
      return
    }
    const amount = reconcileCost.trim()
    const parsed = Number(amount)
    if (!amount || !Number.isFinite(parsed) || parsed < 0) {
      setReconcileError('实际费用需要填写非负数字。')
      return
    }
    reconcile.mutate({
      period_start: isoStart(reconcileStart),
      period_end: isoEnd(reconcileEnd),
      source,
      actual_cost_usd: amount,
    })
  }

  return (
    <section className="page-content cq-page">
      <div className="page-heading cq-heading">
        <div>
          <p className="eyebrow">工作空间 / 成本与质量</p>
          <h1>成本与质量中心</h1>
          <p>按服务端用量账本查看 Token、费用归因、容量健康与评测报告。</p>
        </div>
        <button className="button-primary" type="button" onClick={() => {
          void summary.refetch()
          void usage.refetch()
          void health.refetch()
          if (submittedDataset) void reports.refetch()
          if (tab === 'quota') {
            void effectivePolicy.refetch()
            void adminSettings.refetch()
          }
        }}>刷新数据</button>
      </div>

      <nav className="panel-tabs" role="tablist" aria-label="成本与质量分区">
        {([
          ['cost', '成本', summary.data?.request_count ?? summary.data?.events ?? events.length],
          ['quality', '质量', reportItems.length],
          ['quota', '配额策略', budgetRows.length],
        ] as const).map(([id, label, count]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'panel-tab active' : 'panel-tab'}
            onClick={() => setTab(id)}
          >
            {label}
            <span className="panel-tab-count">{count}</span>
          </button>
        ))}
      </nav>

      {tab === 'cost' && (
      <>
      <div className="cq-filters">
        <label>开始日期<input type="date" value={start} max={end || undefined} onChange={(event) => setStart(event.target.value)} /></label>
        <label>结束日期<input type="date" value={end} min={start || undefined} max={today()} onChange={(event) => setEnd(event.target.value)} /></label>
        <label>模型<select value={model} onChange={(event) => setModel(event.target.value)}><option value="">全部模型</option>{modelOptions.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
        <label>Run ID<input value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="全部 Run" /></label>
        <label>Project ID<input value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="全部项目" /></label>
        <label>Agent ID<input value={agentId} onChange={(event) => setAgentId(event.target.value)} placeholder="全部 Agent" /></label>
        <span className="cq-refresh-note">数据自动刷新 · 30 秒</span>
      </div>
      <p className="cq-footnote">Project / Agent 是显式业务分析筛选，不是权限边界。权限关闭模式下按实例默认租户账本读取，不按用户、部门或成员关系过滤；权限启用模式仍由服务端身份与授权策略控制访问。</p>
      {!rangeValid && <p className="cq-inline-error" role="alert">日期范围无效，请确认开始日期不晚于结束日期。</p>}

      {summary.isPending && rangeValid ? <LoadingState label="正在读取用量账本" /> : summary.isError ? (
        <ErrorState title="用量汇总暂不可用" description={errorText(summary.error)} onRetry={() => void summary.refetch()} />
      ) : summary.data && (
        <>
          <div className="cq-metrics">
            <Metric label="总 Token" value={number(summary.data.total_tokens)} hint={`输入 ${number(summary.data.input_tokens)} · 输出 ${number(summary.data.output_tokens)}`} />
            <Metric label="请求/账本事件" value={number(summary.data.request_count ?? summary.data.events)} hint={summary.data.request_count == null ? "当前接口仅提供账本事件数" : "服务端请求计数"} />
            <Metric label="估算费用" value={usd(summary.data.estimated_cost_usd)} hint="来源：平台估算" />
            <Metric label="供应商报告费用" value={usd(summary.data.provider_reported_cost_usd)} hint="来源：供应商报告" />
            <Metric label="已对账事件费用" value={usd(summary.data.reconciled_event_cost_usd)} hint="来源：已对账" />
            <Metric label="估算与供应商差额" value={usd(summary.data.estimated_vs_provider_actual_variance_usd)} hint="供应商报告费用 − 平台估算费用" />
            <Metric label="成功率" value={summary.data.success_rate == null ? '—' : (summary.data.success_rate * 100).toFixed(2) + '%'} hint="仅显示服务端提供值" />
            <Metric label="错误率" value={summary.data.error_rate == null ? '—' : (summary.data.error_rate * 100).toFixed(2) + '%'} hint="仅显示服务端提供值" />
            <Metric label="P50 / P95 延迟" value={summary.data.p50_latency_ms == null && summary.data.p95_latency_ms == null ? '—' : number(summary.data.p50_latency_ms) + ' / ' + number(summary.data.p95_latency_ms) + ' ms'} hint="服务端未提供时不推算" />
            <Metric label="首 Token P50" value={summary.data.first_token_ms_p50 == null ? '—' : number(summary.data.first_token_ms_p50) + ' ms'} hint="仅显示服务端提供值" />
          </div>
          <section className="cq-panel cq-trends-panel">
            <header><div><h2>Token / 费用趋势</h2><p>按 Usage Ledger 的 occurred_at（UTC 日）聚合；不从总量或模型排行反推时间序列</p></div><span>{dailyTrend.length} 天</span></header>
            {usage.isPending ? <LoadingState label="正在读取趋势账本" /> : usage.isError ? (
              <ErrorState title="趋势数据暂不可用" description={errorText(usage.error)} onRetry={() => void usage.refetch()} />
            ) : dailyTrend.length ? (
              <>
                <div className="cq-trend-grid">
                  <TrendLine label="每日 Token" values={dailyTrend.map((day) => day.totalTokens)} format={number} />
                  <TrendLine label="每日费用（USD）" values={dailyTrend.map((day) => day.costUsd)} format={(value) => usd(String(value))} />
                </div>
                <div className="cq-trend-table-wrap">
                  <table className="cq-table cq-trend-table">
                    <thead><tr><th>日期（UTC）</th><th>输入 Token</th><th>输出 Token</th><th>总 Token</th><th>费用（USD）</th><th>有价 / 缺价事件</th></tr></thead>
                    <tbody>{dailyTrend.map((day) => <tr key={day.date}>
                      <td>{day.date}</td><td>{number(day.inputTokens)}</td><td>{number(day.outputTokens)}</td><td>{number(day.totalTokens)}</td>
                      <td>{day.costUsd == null ? '—' : usd(String(day.costUsd))}</td><td>{number(day.pricedEvents)} / {number(day.unpricedEvents)}</td>
                    </tr>)}</tbody>
                  </table>
                </div>
                <p className="cq-footnote">费用仅累加服务端返回且可解析的 cost_usd；缺价事件单独计数，不视为 0 USD。当前账本请求最多读取 1,000 条，达到上限时趋势可能不完整。</p>
              </>
            ) : <EmptyState title="暂无趋势数据" description="当前筛选范围内没有可用于趋势聚合的用量账本事件。" />}
          </section>
          <div className="cq-panels">
            <section className="cq-panel">
              <header><div><h2>模型费用排行</h2><p>按账本 cost_usd 聚合；不推算缺失价格</p></div></header>
              {byModel.length ? <div className="cq-bars">{byModel.map(([key, item]) => {
                const amount = Number(item.cost_usd)
                const width = maxModelCost > 0 && Number.isFinite(amount) ? Math.max(0, amount / maxModelCost * 100) : 0
                return <div className="cq-bar-row" key={key}><span title={key}>{key}</span><div className="cq-bar-track"><i style={{ width: width + '%' }} /></div><strong>{usd(item.cost_usd)}</strong></div>
              })}</div> : <EmptyState title="暂无模型费用记录" description="当前筛选范围没有可归因的账本事件。" />}
            </section>
            <section className="cq-panel">
              <header><div><h2>项目费用排行</h2><p>按服务端 by_project 聚合；选择项目可下钻到账本</p></div></header>
              {Object.keys(summary.data.by_project ?? {}).length ? <div className="cq-bars">{Object.entries(summary.data.by_project ?? {}).sort((a,b) => Number(b[1].cost_usd)-Number(a[1].cost_usd)).map(([id,item]) => <button type="button" className="cq-bar-row cq-drilldown" key={id} onClick={() => setProjectId(projectId === id ? '' : id)}><span>{id}</span><span>{number(item.events)} 条</span><strong>{usd(item.cost_usd)}</strong></button>)}</div> : <EmptyState title="暂无项目归因" description="账本没有提供可归因的 project_id。" />}
            </section>
            <section className="cq-panel">
              <header><div><h2>Agent 费用排行</h2><p>按服务端 by_agent 聚合；选择 Agent 可下钻到账本</p></div></header>
              {Object.keys(summary.data.by_agent ?? {}).length ? <div className="cq-bars">{Object.entries(summary.data.by_agent ?? {}).sort((a,b) => Number(b[1].cost_usd)-Number(a[1].cost_usd)).map(([id,item]) => <button type="button" className="cq-bar-row cq-drilldown" key={id} onClick={() => setAgentId(agentId === id ? '' : id)}><span>{id}</span><span>{number(item.events)} 条</span><strong>{usd(item.cost_usd)}</strong></button>)}</div> : <EmptyState title="暂无 Agent 归因" description="账本没有提供可归因的 agent_id。" />}
            </section>
            <section className="cq-panel">
              <header><div><h2>容量健康</h2><p>服务端探针状态，不从浏览器推算</p></div></header>
              {health.isPending ? <LoadingState label="正在检查容量健康" /> : health.isError ? <div className="cq-unavailable"><strong>容量探针不可用</strong><p>{errorText(health.error)}</p></div> : <div className="cq-health"><strong data-status={health.data.status}>{valueLabel(health.data.status)}</strong><pre>{localizedJson(health.data)}</pre></div>}
            </section>
          </div>
          <section className="cq-panel cq-usage-panel">
            <header><div><h2>用量明细</h2><p>事件来源、运行、模型和成本均来自服务端账本</p></div><div className="cq-usage-actions"><span>{number(events.length)} 条</span><button className="button-secondary" type="button" disabled={downloadCsv.isPending || !rangeValid} onClick={() => downloadCsv.mutate()}>{downloadCsv.isPending ? '导出中…' : '导出当前筛选 CSV'}</button></div></header>
            {downloadCsv.isError && <p className="cq-inline-error" role="alert">{quotaErrorText(downloadCsv.error)}</p>}
            {usage.isPending ? <LoadingState label="正在加载用量明细" /> : usage.isError ? <ErrorState title="用量明细加载失败" description={errorText(usage.error)} onRetry={() => void usage.refetch()} /> : events.length ? (
              <>
                <div className="cq-table-wrap"><table className="cq-table"><thead><tr><th>发生时间</th><th>模型</th><th>运行</th><th>输入 Token</th><th>输出 Token</th><th>缓存 Token</th><th>推理 Token</th><th>费用（美元）</th><th>计价口径</th><th>状态/错误</th><th>来源</th></tr></thead><tbody>{events.slice((usagePage - 1) * usagePageSize, usagePage * usagePageSize).map((event) => <tr key={event.event_id}><td>{event.occurred_at ? formatDateTime(event.occurred_at) : '—'}</td><td>{event.model_key || '—'}</td><td>{event.run_id ? <Link className="text-link" to={`/operations/runs/${encodeURIComponent(event.run_id)}`}>{event.run_id}</Link> : '—'}</td><td>{number(event.input_tokens)}</td><td>{number(event.output_tokens)}</td><td>{number(event.cached_tokens)}</td><td>{number(event.reasoning_tokens)}</td><td>{usd(event.cost_usd)}</td><td>{event.cost_basis ? valueLabel(event.cost_basis) : 'USD · ' + valueLabel(event.source)}{event.pricing_version ? ' · ' + event.pricing_version : ''}</td><td>{event.status ? valueLabel(event.status) : '—'}{event.error_code ? ' / ' + event.error_code : ''}</td><td>{valueLabel(event.source)}</td></tr>)}</tbody></table></div>
                <Pagination total={events.length} page={usagePage} pageSize={usagePageSize} onPageChange={setUsagePage} onPageSizeChange={(n) => { setUsagePageSize(n); setUsagePage(1) }} />
              </>
            ) : <EmptyState title="暂无用量事件" description="此时间范围没有服务端账本记录；不会以 0 元代替缺失价格。" />}
            <p className="cq-footnote">工具调用数：{number(summary.data.tool_calls)} · 运行时长：{number(summary.data.runtime_ms)} 毫秒 · 存储：{number(summary.data.storage_bytes)} 字节。工具与存储计价：{summary.data.tool_and_storage_pricing}。</p>
          </section>
        </>
      )}
      </>
      )}

      {tab === 'quality' && (
      <section className="cq-panel cq-evaluation">
        <header><div><h2>质量评测报告</h2><p>查询指定数据集的服务端评测历史；召回率 / 通过率不在前端重算。</p></div></header>
        <form className="cq-dataset-form" onSubmit={(event) => { event.preventDefault(); setSubmittedDataset(datasetId.trim()) }}>
          <label>数据集 ID<input value={datasetId} onChange={(event) => setDatasetId(event.target.value)} placeholder="输入已有数据集 ID" /></label>
          <button className="button-primary" type="submit" disabled={!datasetId.trim()}>查询报告</button>
        </form>
        {reports.isPending && submittedDataset ? <LoadingState label="正在读取评测报告" /> : reports.isError ? <ErrorState title="评测报告不可用" description={errorText(reports.error)} onRetry={() => void reports.refetch()} /> : submittedDataset && reportItems.length ? (
          <div className="cq-report-list">{reportItems.map((item, index) => {
          const runId = String(item.eval_run_id ?? item.evaluation_run_id ?? item.id ?? '')
          const metrics = Object.entries(item).filter(([key, value]) => key !== 'cases' && key !== 'results' && (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'))
          const results = Array.isArray(item.results) ? item.results : Array.isArray(item.cases) ? item.cases : []
          return <article key={runId || index}><strong>{runId || '评测记录'}</strong><span>{metrics.map(([key, value]) => fieldLabel(key) + '：' + displayValue(key, value)).join(' · ')}</span>{runId && <Link className="text-link" to={`/operations/evaluation/runs/${encodeURIComponent(runId)}`}>查看运行与样本证据（{results.length || '详情'}）</Link>}</article>
        })}</div>
        ) : submittedDataset ? <EmptyState title="没有评测报告" description="该数据集当前没有可读取的评测历史，或服务端未返回记录。" /> : <EmptyState title="选择数据集查看质量结果" description="输入数据集 ID 后查询评测报告；不会展示虚构的准确率、召回率或回归分数。" />}
      </section>
      )}

      {tab === 'quota' && (
      <>
        <section className="cq-panel">
          <header><div><h2>生效配额策略</h2><p>按项目 / 对象维度解析生效上限；未填写时返回租户级策略。来源与版本均来自服务端。</p></div></header>
          <form className="cq-dataset-form" onSubmit={submitPolicy}>
            <label>项目 ID<input value={policyProjectId} onChange={(event) => setPolicyProjectId(event.target.value)} placeholder="可选，用于解析项目级策略" /></label>
            <label>对象 ID<input value={policySubjectId} onChange={(event) => setPolicySubjectId(event.target.value)} placeholder="可选，用于解析对象级策略" /></label>
            <button className="button-primary" type="submit">解析生效策略</button>
          </form>
          {effectivePolicy.isPending ? <LoadingState label="正在解析生效配额策略" /> : effectivePolicy.isError ? (
            <ErrorState title="生效配额策略不可用" description={quotaErrorText(effectivePolicy.error)} onRetry={() => void effectivePolicy.refetch()} />
          ) : effectivePolicy.data && Object.keys(effectivePolicy.data.limits ?? {}).length > 0 ? (
            <>
              <p className="cq-quota-meta">租户：{effectivePolicy.data.tenant_id || '—'} · 解析时间：{formatDateTime(effectivePolicy.data.resolved_at)}</p>
              <div className="cq-table-wrap">
                <table className="cq-table">
                  <thead><tr><th>配额维度</th><th>生效上限</th><th>来源策略</th><th>策略版本</th></tr></thead>
                  <tbody>
                    {Object.entries(effectivePolicy.data.limits).map(([key, value]) => (
                      <tr key={key}>
                        <td>{DIMENSION_LABELS[key] ?? fieldLabel(key)}</td>
                        <td>{limitText(value)}</td>
                        <td>{valueLabel(effectivePolicy.data?.sources?.[key])}</td>
                        <td>{effectivePolicy.data?.policy_versions?.[key] ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <EmptyState title="当前范围没有生效配额上限" description="服务端未返回任何配额维度；这表示没有配置策略，不代表用量为零。" />
          )}
        </section>

        <section className="cq-panel">
          <header><div><h2>配额预算</h2><p>来自平台管理设置中的 platform_quota_budgets，仅展示当前租户。</p></div><span>{number(budgetRows.length)} 条</span></header>
          {adminSettings.isPending ? <LoadingState label="正在读取配额预算" /> : adminSettings.isError ? (
            <ErrorState title="配额预算不可用" description={quotaErrorText(adminSettings.error)} onRetry={() => void adminSettings.refetch()} />
          ) : budgetRows.length === 0 ? (
            <EmptyState title="暂无配额预算" description="当前租户还没有预算记录；可在系统设置中保存配额后回到本页查看。" />
          ) : (
            <div className="cq-table-wrap">
              <table className="cq-table">
                <thead><tr><th>范围类型</th><th>范围</th><th>周期</th><th>硬性 Token</th><th>硬性费用（美元）</th><th>并发</th><th>软性 Token</th><th>软性费用（美元）</th><th>更新时间</th></tr></thead>
                <tbody>
                  {budgetRows.map((row, index) => (
                    <tr key={`${String(row.scope_type ?? '')}:${String(row.scope_id ?? '')}:${index}`}>
                      <td>{valueLabel(row.scope_type)}</td>
                      <td>{String(row.scope_id ?? '—')}</td>
                      <td>{formatDateTime(row.period_start)} ~ {formatDateTime(row.period_end)}</td>
                      <td>{limitText(row.hard_tokens)}</td>
                      <td>{moneyText(row.hard_cost_usd)}</td>
                      <td>{limitText(row.hard_concurrency)}</td>
                      <td>{limitText(row.soft_tokens)}</td>
                      <td>{moneyText(row.soft_cost_usd)}</td>
                      <td>{formatDateTime(row.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="cq-panel">
          <header><div><h2>用量对账</h2><p>提交账单侧实际费用，由服务端写入对账记录；返回结果原样展示，不在前端推算差额。</p></div></header>
          <form className="cq-dataset-form" onSubmit={submitReconcile}>
            <label>周期开始<input type="date" value={reconcileStart} max={reconcileEnd || undefined} onChange={(event) => setReconcileStart(event.target.value)} /></label>
            <label>周期结束<input type="date" value={reconcileEnd} min={reconcileStart || undefined} onChange={(event) => setReconcileEnd(event.target.value)} /></label>
            <label>对账来源<input value={reconcileSource} onChange={(event) => setReconcileSource(event.target.value)} placeholder="例如：provider-invoice-2026-11" maxLength={128} /></label>
            <label>实际费用（美元）<input type="number" min={0} step="0.000001" value={reconcileCost} onChange={(event) => setReconcileCost(event.target.value)} placeholder="非负数字" /></label>
            <button className="button-primary" type="submit" disabled={reconcile.isPending}>{reconcile.isPending ? '提交中…' : '提交对账'}</button>
          </form>
          {reconcileError && <p className="cq-inline-error" role="alert">{reconcileError}</p>}
          {reconcile.isSuccess && (
            <div className="cq-quota-result">
              <p className="cq-inline-success" role="status">对账已提交，服务端返回结果如下。</p>
              <pre>{localizedJson(reconcile.data ?? {})}</pre>
            </div>
          )}
        </section>

        <section className="cq-panel">
          <header><div><h2>用量 CSV 导出</h2><p>使用下方日期与模型筛选调用服务端导出接口；同源 Cookie 会话随请求发送。</p></div></header>
          <div className="cq-dataset-form">
            <label>开始日期<input type="date" value={start} max={end || undefined} onChange={(event) => setStart(event.target.value)} /></label>
            <label>结束日期<input type="date" value={end} min={start || undefined} max={today()} onChange={(event) => setEnd(event.target.value)} /></label>
            <label>模型<select value={model} onChange={(event) => setModel(event.target.value)}><option value="">全部模型</option>{modelOptions.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
            <button className="button-primary" type="button" disabled={!rangeValid || downloadCsv.isPending} onClick={() => downloadCsv.mutate()}>{downloadCsv.isPending ? '导出中…' : '下载 CSV'}</button>
          </div>
          {!rangeValid && <p className="cq-inline-error" role="alert">日期范围无效，请确认开始日期不晚于结束日期。</p>}
          {downloadCsv.isError && <p className="cq-inline-error" role="alert">{quotaErrorText(downloadCsv.error)}</p>}
          {downloadCsv.isSuccess && <p className="cq-inline-success" role="status">已下载 CSV，共 {number(downloadCsv.data)} 字节。</p>}
          <p className="cq-footnote">导出路径：GET /platform/v1/quotas/usage.csv；CSV 列名与用量接口字段一致，缺失的可选归属字段由服务端留空。</p>
        </section>
      </>
      )}

      <p className="cq-footnote">数据口径：费用 API 当前仅提供 USD 及 estimated / provider_reported / reconciled 来源；P50/P95、TTFT、cached/reasoning token、按 Agent/项目费用穿透和质量评测样本级证据仍需核对独立 API 契约后接入。本页不将缺失字段推算为零。</p>
    </section>
  )
}
