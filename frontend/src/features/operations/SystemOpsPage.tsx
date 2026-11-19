import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'

import { ApiError, apiClient } from '@/lib/api'

type HealthResult = {
  status?: string
  checks?: Record<string, string>
  reason?: string
  [key: string]: unknown
}

type SchedulerJob = {
  job_id?: string
  id?: string
  name?: string
  title?: string
  enabled?: boolean
  status?: string
  next_run_at?: string | null
  last_run_at?: string | null
  [key: string]: unknown
}

type SchedulerRun = {
  run_id?: string
  id?: string
  job_id?: string
  status?: string
  started_at?: string
  finished_at?: string
  created_at?: string
  [key: string]: unknown
}

type ItemsResponse<T> = { items?: T[]; count?: number; feishu?: Record<string, unknown> }

type Metric = { name: string; value: string; labels: string }

function parseMetrics(source: string | undefined): Metric[] {
  if (!source) return []
  return source
    .split('\n')
    .filter((line) => line && !line.startsWith('#'))
    .map((line) => {
      const match = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(.+)$/)
      return match
        ? { name: match[1], labels: match[2] ?? '', value: match[3] }
        : null
    })
    .filter((item): item is Metric => item !== null)
    .slice(0, 80)
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '启用' : '停用'
  if (typeof value === 'number') return new Intl.NumberFormat().format(value)
  return String(value)
}

function time(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}（HTTP ${error.status}${error.requestId ? ` · ${error.requestId}` : ''}）`
  return error instanceof Error ? error.message : '请求失败'
}

function Status({ value }: { value: unknown }) {
  const normalized = String(value ?? 'unknown').toLowerCase()
  const tone = ['ok', 'ready', 'healthy', 'success', 'completed', 'enabled', 'idle', 'succeeded'].includes(normalized)
    ? 'good'
    : ['warning', 'pending', 'running', 'queued'].includes(normalized)
      ? 'warning'
      : ['unknown', '—', '', 'loading', 'disabled', 'skipped'].includes(normalized)
        ? 'neutral'
        : 'bad'
  return <span className={`system-ops-status ${tone}`}>{display(value)}</span>
}

function Panel({
  title,
  endpoint,
  children,
}: {
  title: string
  endpoint: string
  children: ReactNode
}) {
  return (
    <section className="system-ops-panel">
      <header className="system-ops-panel-header">
        <h2>{title}</h2>
        <code>{endpoint}</code>
      </header>
      {children}
    </section>
  )
}

export function SystemOpsPage() {
  const readiness = useQuery({
    queryKey: ['system-ops', 'readiness'],
    queryFn: () => apiClient.get<HealthResult>('/health/ready'),
    refetchInterval: 30_000,
    retry: false,
  })
  const capacity = useQuery({
    queryKey: ['system-ops', 'capacity'],
    queryFn: () => apiClient.get<HealthResult>('/platform/v1/observability/health/capacity'),
    refetchInterval: 30_000,
    retry: false,
  })
  const metrics = useQuery({
    queryKey: ['system-ops', 'metrics'],
    queryFn: () => apiClient.get<string>('/platform/v1/observability/metrics'),
    refetchInterval: 60_000,
    retry: false,
  })
  const jobs = useQuery({
    queryKey: ['system-ops', 'scheduler-jobs'],
    queryFn: () => apiClient.get<ItemsResponse<SchedulerJob>>('/platform/v1/scheduler/jobs'),
    refetchInterval: 30_000,
    retry: false,
  })
  const runs = useQuery({
    queryKey: ['system-ops', 'scheduler-runs'],
    queryFn: () => apiClient.get<ItemsResponse<SchedulerRun>>('/platform/v1/scheduler/runs?limit=8'),
    refetchInterval: 30_000,
    retry: false,
  })

  const metricRows = useMemo(() => parseMetrics(metrics.data), [metrics.data])
  const metricHighlights = useMemo(
    () => metricRows.filter((metric) => /^(process_|python_|http_requests_|scheduler_)/.test(metric.name)).slice(0, 8),
    [metricRows],
  )
  const refreshAll = () => {
    void readiness.refetch()
    void capacity.refetch()
    void metrics.refetch()
    void jobs.refetch()
    void runs.refetch()
  }

  return (
    <section className="page-content system-ops-page">
      <div className="system-ops-heading">
        <div>
          <p className="eyebrow">AGENT PLATFORM / SYSTEM OPERATIONS</p>
          <h1>系统运维</h1>
          <p>运行健康、Prometheus 指标与定时调度状态。数据直接来自服务端只读接口。</p>
        </div>
        <button type="button" className="system-ops-refresh" onClick={refreshAll}>刷新数据</button>
      </div>

      <div className="system-ops-summary">
        <article className="system-ops-summary-card">
          <span>服务就绪</span>
          <Status value={readiness.data?.status ?? (readiness.isError ? 'unavailable' : 'loading')} />
          <small>{readiness.data?.reason ?? (readiness.isError ? errorMessage(readiness.error) : '依赖检查状态')}</small>
        </article>
        <article className="system-ops-summary-card">
          <span>容量健康</span>
          <Status value={capacity.data?.status ?? (capacity.isError ? 'unavailable' : 'loading')} />
          <small>{capacity.data?.message ? display(capacity.data.message) : '容量监控器评估结果'}</small>
        </article>
        <article className="system-ops-summary-card">
          <span>定时作业</span>
          <strong>{jobs.data?.items?.length ?? '—'}</strong>
          <small>{jobs.isError ? errorMessage(jobs.error) : '当前租户可见的作业数量'}</small>
        </article>
        <article className="system-ops-summary-card">
          <span>最近调度执行</span>
          <strong>{runs.data?.items?.length ?? '—'}</strong>
          <small>{runs.isError ? errorMessage(runs.error) : '最近 8 条执行记录'}</small>
        </article>
      </div>

      <div className="system-ops-grid">
        <Panel title="容量健康" endpoint="GET /platform/v1/observability/health/capacity">
          {capacity.isLoading ? <p className="system-ops-muted">正在读取容量状态…</p> : capacity.isError ? (
            <p className="system-ops-error" role="alert">{errorMessage(capacity.error)}</p>
          ) : (
            <>
              <div className="system-ops-status-line"><span>整体状态</span><Status value={capacity.data?.status} /></div>
              {Object.entries(capacity.data ?? {}).filter(([key]) => key !== 'status').map(([key, value]) => (
                <div className="system-ops-status-line" key={key}><span>{key}</span><strong>{display(value)}</strong></div>
              ))}
            </>
          )}
        </Panel>
      </div>

      <Panel title="Prometheus 指标" endpoint="GET /platform/v1/observability/metrics">
        {metrics.isLoading ? <p className="system-ops-muted">正在读取指标…</p> : metrics.isError ? (
          <p className="system-ops-error" role="alert">{errorMessage(metrics.error)}</p>
        ) : metricHighlights.length ? (
          <div className="system-ops-metrics">
            {metricHighlights.map((metric, index) => (
              <article className="system-ops-metric" key={`${metric.name}-${index}`}>
                <span>{metric.name}</span>
                <strong>{metric.value}</strong>
                {metric.labels && <small>{metric.labels}</small>}
              </article>
            ))}
          </div>
        ) : (
          <p className="system-ops-muted">{metricRows.length ? `已读取 ${metricRows.length} 条指标；当前未发现可展示的进程/请求/调度指标。` : '指标端点已响应，但没有可解析的指标行。'}</p>
        )}
      </Panel>

      <div className="system-ops-grid">
        <Panel title="定时作业" endpoint="GET /platform/v1/scheduler/jobs">
          {jobs.isLoading ? <p className="system-ops-muted">正在读取作业…</p> : jobs.isError ? (
            <p className="system-ops-error" role="alert">{errorMessage(jobs.error)}</p>
          ) : jobs.data?.items?.length ? (
            <div className="system-ops-table-wrap">
              <table className="system-ops-table">
                <thead><tr><th>作业</th><th>状态</th><th>下次执行</th><th>最近执行</th></tr></thead>
                <tbody>{jobs.data.items.map((job, index) => (
                  <tr key={job.job_id ?? job.id ?? `job-${index}`}>
                    <td><strong>{display(job.name ?? job.title ?? job.job_id ?? job.id)}</strong><small>{display(job.job_id ?? job.id)}</small></td>
                    <td><Status value={job.enabled === false ? 'disabled' : job.status ?? (job.enabled ? 'enabled' : 'unknown')} /></td>
                    <td>{time(job.next_run_at)}</td>
                    <td>{time(job.last_run_at)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : <p className="system-ops-muted">当前租户暂无定时作业。</p>}
        </Panel>

        <Panel title="最近调度执行" endpoint="GET /platform/v1/scheduler/runs?limit=8">
          {runs.isLoading ? <p className="system-ops-muted">正在读取执行记录…</p> : runs.isError ? (
            <p className="system-ops-error" role="alert">{errorMessage(runs.error)}</p>
          ) : runs.data?.items?.length ? (
            <div className="system-ops-run-list">
              {runs.data.items.map((run, index) => (
                <article className="system-ops-run" key={run.run_id ?? run.id ?? `run-${index}`}>
                  <div><strong>{display(run.job_id ?? run.run_id ?? run.id)}</strong><Status value={run.status} /></div>
                  <small>{time(run.started_at ?? run.created_at)}{run.finished_at ? ` → ${time(run.finished_at)}` : ''}</small>
                </article>
              ))}
            </div>
          ) : <p className="system-ops-muted">暂无调度执行记录。</p>}
        </Panel>
      </div>

      <p className="system-ops-footnote">自动刷新：健康与调度 30 秒，Prometheus 指标 60 秒。权限拒绝、服务未配置和网络错误会作为独立错误状态显示，不会被转换成健康绿灯。</p>
    </section>
  )
}
