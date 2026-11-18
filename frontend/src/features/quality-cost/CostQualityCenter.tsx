import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api'

type UsageEvent = {
  event_id: string
  run_id?: string
  model_key: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: string
  project_id?: string | null
  agent_id?: string | null
  runtime_ms: number
  source: string
  occurred_at: string
}
type UsageSummary = {
  events: number
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
  price_card_versions: string[]
  tool_and_storage_pricing: string
}
type ListResponse<T> = { items?: T[]; data?: T[]; count?: number }
type Health = { status: 'ok' | 'warning' | 'critical' | 'unavailable'; [key: string]: unknown }
type EvaluationReport = Record<string, unknown>

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
const params = (start: string, end: string, model?: string) => {
  const search = new URLSearchParams({ start: isoStart(start), end: isoEnd(end) })
  if (model) search.set('model_key', model)
  return search.toString()
}
const number = (value?: number) =>
  value == null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat().format(value)
const usd = (value?: string) => {
  if (value == null || value === '') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 6 }).format(parsed)
    : '—'
}
const errorText = (error: unknown) => error instanceof Error ? error.message : '请求失败'
const unwrapItems = <T,>(value: ListResponse<T> | T[]): T[] =>
  Array.isArray(value) ? value : Array.isArray(value.items) ? value.items : Array.isArray(value.data) ? value.data : []

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <article className="cq-metric"><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</article>
}

export function CostQualityCenter() {
  const [start, setStart] = useState(defaultStart)
  const [end, setEnd] = useState(today)
  const [model, setModel] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [submittedDataset, setSubmittedDataset] = useState('')

  const rangeValid = Boolean(start && end && start <= end)
  const query = params(start, end, model || undefined)
  const summary = useQuery({
    queryKey: ['quality-cost', 'summary', start, end, model],
    queryFn: () => apiClient.get<UsageSummary>(`/platform/v1/quotas/usage/summary?${query}`),
    enabled: rangeValid,
    refetchInterval: 30_000,
  })
  const usage = useQuery({
    queryKey: ['quality-cost', 'usage', start, end, model],
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

  const events = useMemo(() => unwrapItems(usage.data ?? []), [usage.data])
  const modelOptions = useMemo(
    () => [...new Set(events.map((event) => event.model_key).filter(Boolean))].sort(),
    [events],
  )
  const byModel = Object.entries(summary.data?.by_model ?? {}).sort(
    (a, b) => Number(b[1].cost_usd) - Number(a[1].cost_usd),
  )
  const maxModelCost = Math.max(0, ...byModel.map(([, item]) => Number(item.cost_usd) || 0))
  const reportItems = unwrapItems(reports.data ?? [])

  return (
    <section className="page-content cq-page">
      <div className="page-heading cq-heading">
        <div>
          <p className="eyebrow">WORKSPACE / COST & QUALITY</p>
          <h1>成本与质量中心</h1>
          <p>按服务端用量账本查看 Token、费用归因、容量健康与评测报告。</p>
        </div>
        <button className="button-primary" type="button" onClick={() => {
          void summary.refetch()
          void usage.refetch()
          void health.refetch()
          if (submittedDataset) void reports.refetch()
        }}>刷新数据</button>
      </div>

      <div className="cq-filters">
        <label>开始日期<input type="date" value={start} max={end || undefined} onChange={(event) => setStart(event.target.value)} /></label>
        <label>结束日期<input type="date" value={end} min={start || undefined} max={today()} onChange={(event) => setEnd(event.target.value)} /></label>
        <label>模型<select value={model} onChange={(event) => setModel(event.target.value)}><option value="">全部模型</option>{modelOptions.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
        <span className="cq-refresh-note">数据自动刷新 · 30 秒</span>
      </div>
      {!rangeValid && <p className="cq-inline-error" role="alert">日期范围无效，请确认开始日期不晚于结束日期。</p>}

      {summary.isPending && rangeValid ? <LoadingState label="正在读取用量账本" /> : summary.isError ? (
        <ErrorState title="用量汇总暂不可用" description={errorText(summary.error)} onRetry={() => void summary.refetch()} />
      ) : summary.data && (
        <>
          <div className="cq-metrics">
            <Metric label="总 Token" value={number(summary.data.total_tokens)} hint={`输入 ${number(summary.data.input_tokens)} · 输出 ${number(summary.data.output_tokens)}`} />
            <Metric label="账本事件" value={number(summary.data.events)} hint="按事件计数，不等同于请求数" />
            <Metric label="估算费用" value={usd(summary.data.estimated_cost_usd)} hint="source=estimated" />
            <Metric label="供应商报告费用" value={usd(summary.data.provider_reported_cost_usd)} hint="source=provider_reported" />
            <Metric label="已对账事件费用" value={usd(summary.data.reconciled_event_cost_usd)} hint="source=reconciled" />
            <Metric label="估算与供应商差额" value={usd(summary.data.estimated_vs_provider_actual_variance_usd)} hint="供应商报告费用 − 平台估算费用" />
          </div>
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
              <header><div><h2>容量健康</h2><p>服务端探针状态，不从浏览器推算</p></div></header>
              {health.isPending ? <LoadingState label="正在检查容量健康" /> : health.isError ? <div className="cq-unavailable"><strong>容量探针不可用</strong><p>{errorText(health.error)}</p></div> : <div className="cq-health"><strong data-status={health.data.status}>{health.data.status}</strong><pre>{JSON.stringify(health.data, null, 2)}</pre></div>}
            </section>
          </div>
          <section className="cq-panel cq-usage-panel">
            <header><div><h2>用量明细</h2><p>事件来源、Run、模型和成本均来自服务端账本</p></div><span>{number(events.length)} 条</span></header>
            {usage.isPending ? <LoadingState label="正在加载用量明细" /> : usage.isError ? <ErrorState title="用量明细加载失败" description={errorText(usage.error)} onRetry={() => void usage.refetch()} /> : events.length ? (
              <div className="cq-table-wrap"><table className="cq-table"><thead><tr><th>发生时间</th><th>模型</th><th>Run</th><th>输入 Token</th><th>输出 Token</th><th>费用 (USD)</th><th>来源</th></tr></thead><tbody>{events.map((event) => <tr key={event.event_id}><td>{event.occurred_at ? new Date(event.occurred_at).toLocaleString() : '—'}</td><td>{event.model_key || '—'}</td><td>{event.run_id || '—'}</td><td>{number(event.input_tokens)}</td><td>{number(event.output_tokens)}</td><td>{usd(event.cost_usd)}</td><td>{event.source || '—'}</td></tr>)}</tbody></table></div>
            ) : <EmptyState title="暂无用量事件" description="此时间范围没有服务端账本记录；不会以 0 元代替缺失价格。" />}
            <p className="cq-footnote">工具调用数：{number(summary.data.tool_calls)} · 运行时长：{number(summary.data.runtime_ms)} ms · 存储：{number(summary.data.storage_bytes)} bytes。工具与存储计价：{summary.data.tool_and_storage_pricing}。</p>
          </section>
        </>
      )}

      <section className="cq-panel cq-evaluation">
        <header><div><h2>质量评测报告</h2><p>查询指定 Dataset 的服务端评测历史；Recall@K / pass rate 不在前端重算。</p></div></header>
        <form className="cq-dataset-form" onSubmit={(event) => { event.preventDefault(); setSubmittedDataset(datasetId.trim()) }}>
          <label>Dataset ID<input value={datasetId} onChange={(event) => setDatasetId(event.target.value)} placeholder="输入已有 dataset_id" /></label>
          <button className="button-primary" type="submit" disabled={!datasetId.trim()}>查询报告</button>
        </form>
        {reports.isPending && submittedDataset ? <LoadingState label="正在读取评测报告" /> : reports.isError ? <ErrorState title="评测报告不可用" description={errorText(reports.error)} onRetry={() => void reports.refetch()} /> : submittedDataset && reportItems.length ? (
          <div className="cq-report-list">{reportItems.map((item, index) => <article key={String(item.eval_run_id ?? item.evaluation_run_id ?? item.id ?? index)}><strong>{String(item.eval_run_id ?? item.evaluation_run_id ?? item.id ?? '评测记录')}</strong><span>{Object.entries(item).filter(([key, value]) => key !== 'cases' && (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean')).map(([key, value]) => key + ': ' + String(value)).join(' · ')}</span></article>)}</div>
        ) : submittedDataset ? <EmptyState title="没有评测报告" description="该 Dataset 当前没有可读取的评测历史，或服务端未返回记录。" /> : <EmptyState title="选择 Dataset 查看质量结果" description="输入 Dataset ID 后查询评测报告；不会展示虚构的准确率、召回率或回归分数。" />}
      </section>
      <p className="cq-footnote">数据口径：费用 API 当前仅提供 USD 及 estimated / provider_reported / reconciled 来源；P50/P95、TTFT、cached/reasoning token、按 Agent/项目费用穿透和质量评测样本级证据仍需核对独立 API 契约后接入。本页不将缺失字段推算为零。</p>
    </section>
  )
}
