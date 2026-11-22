import { useQuery } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/api-client'
import './system-ops-page.css'

type Envelope<T> = { items?: T[]; count?: number }
type Metric = Record<string, unknown>
type HealthResult = {
  status?: string
  checks?: Record<string, string>
  reason?: string
  [key: string]: unknown
}
type SchedulerJob = {
  job_id?: string
  name?: string
  enabled?: boolean
  status?: string
  interval_seconds?: number
  failure_count?: number
  last_result?: string
  last_message?: string
  last_run_at?: string
  next_run_at?: string
  [key: string]: unknown
}
type SchedulerRun = {
  run_id?: string
  job_id?: string
  status?: string
  started_at?: string
  finished_at?: string
  message?: string
  [key: string]: unknown
}

const endpoints = {
  metrics: '/platform/v1/observability/metrics/json',
  capacity: '/platform/v1/observability/health/capacity',
  readiness: '../health/ready',
  jobs: '/platform/v1/scheduler/jobs',
  runs: '/platform/v1/scheduler/runs?limit=20',
}

function listFrom<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object' && Array.isArray((value as Envelope<T>).items)) {
    return (value as Envelope<T>).items ?? []
  }
  return []
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return value.toLocaleString('zh-CN')
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function statusClass(value: unknown): string {
  const status = typeof value === 'string' ? value.toLowerCase() : 'unknown'
  if (['failed', 'error', 'critical', 'unavailable', 'timed_out'].includes(status)) {
    return 'ops-status ops-status--critical'
  }
  if (['warning', 'retrying'].includes(status)) return 'ops-status ops-status--warning'
  if (['succeeded', 'success', 'ok', 'ready', 'idle'].includes(status)) {
    return 'ops-status ops-status--ok'
  }
  return 'ops-status'
}

function timestamp(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN')
}

export function getQueryFailureDetails(label: string, error: Error) {
  const detail = error as Error & { status?: number; code?: string; requestId?: string }
  return {
    title: `${label}读取失败`,
    message: error.message || '服务端未返回错误详情',
    status: detail.status,
    code: detail.code,
    requestId: detail.requestId,
  }
}

function QueryState({ label, pending, error, onRetry }: {
  label: string
  pending: boolean
  error: Error | null
  onRetry: () => void
}) {
  if (pending) return <p className="ops-muted" role="status">{label}正在读取数据…</p>
  if (!error) return null
  const details = getQueryFailureDetails(label, error)
  return (
    <div className="ops-error" role="alert" aria-live="assertive">
      <strong>{details.title}</strong>
      <span>{details.message}</span>
      {(details.status !== undefined || details.code) && (
        <small>HTTP {details.status ?? '—'}{details.code ? ` · ${details.code}` : ''}</small>
      )}
      {details.requestId && <small>Request ID: {details.requestId}</small>}
      <button type="button" onClick={onRetry}>重试</button>
    </div>
  )
}

export function SystemOpsPage() {
  const metrics = useQuery({
    queryKey: ['system-ops', 'metrics'],
    queryFn: async () => apiClient.get<unknown>(endpoints.metrics),
    refetchInterval: 30000,
    retry: false,
  })
  const capacity = useQuery({
    queryKey: ['system-ops', 'capacity-health'],
    queryFn: async () => apiClient.get<HealthResult>(endpoints.capacity),
    refetchInterval: 15000,
    retry: false,
  })
  const readiness = useQuery({
    queryKey: ['system-ops', 'readiness'],
    queryFn: async () => apiClient.get<HealthResult>(endpoints.readiness),
    refetchInterval: 15000,
    retry: false,
  })
  const jobs = useQuery({
    queryKey: ['system-ops', 'scheduler-jobs'],
    queryFn: async () => apiClient.get<unknown>(endpoints.jobs),
    refetchInterval: 30000,
    retry: false,
  })
  const runs = useQuery({
    queryKey: ['system-ops', 'scheduler-runs'],
    queryFn: async () => apiClient.get<unknown>(endpoints.runs),
    refetchInterval: 30000,
    retry: false,
  })

  const metricRows = listFrom<Metric>(metrics.data)
  const jobRows = listFrom<SchedulerJob>(jobs.data)
  const runRows = listFrom<SchedulerRun>(runs.data)
  const capacityChecks = capacity.data?.checks && typeof capacity.data.checks === 'object'
    ? capacity.data.checks
    : {}
  const readinessChecks = readiness.data?.checks && typeof readiness.data.checks === 'object'
    ? readiness.data.checks
    : {}

  return (
    <main className="system-ops-page">
      <header className="ops-heading">
        <div>
          <p className="ops-eyebrow">平台管理 · #813</p>
          <h1>系统运维</h1>
          <p>Metrics、组件健康与 Scheduler 运行状态。各区块独立请求和刷新，接口不可用时明确显示，不推断为正常。</p>
        </div>
        <span className="ops-refresh">Metrics / Scheduler 30 秒 · Health 15 秒刷新</span>
      </header>

      <section className="ops-panel">
        <div className="ops-panel-heading">
          <div><h2>Metrics 指标</h2><p>来源：Observability JSON exporter</p></div>
          <span>{metrics.dataUpdatedAt ? `更新于 ${new Date(metrics.dataUpdatedAt).toLocaleTimeString('zh-CN')}` : '尚未更新'}</span>
        </div>
        <QueryState label="Metrics" pending={metrics.isPending} error={metrics.error} onRetry={() => void metrics.refetch()} />
        {!metrics.isError && metrics.data !== undefined && (
          metricRows.length ? (
            <div className="ops-metric-grid">
              {metricRows.map((metric, index) => {
                const entries = Object.entries(metric)
                const key = String(metric.name ?? metric.metric ?? metric.key ?? `metric-${index}`)
                const value = metric.value ?? metric.count ?? metric.total ?? metric.samples
                return (
                  <article className="ops-metric-card" key={key}>
                    <span>{display(metric.display_name ?? metric.name ?? metric.metric ?? metric.key ?? key)}</span>
                    <strong>{display(value ?? metric)}</strong>
                    {metric.unit !== undefined && <small>{display(metric.unit)}</small>}
                    {(value === null || value === undefined) && entries.length > 1 && <small>{entries.length} 个返回字段</small>}
                  </article>
                )
              })}
            </div>
          ) : <p className="ops-muted">接口已响应，但当前没有可展示的指标记录；不以 0 代替缺失数据。</p>
        )}
      </section>

      <section className="ops-panel">
        <div className="ops-panel-heading">
          <div><h2>Health 健康状态</h2><p>容量监控与服务 readiness 探针</p></div>
        </div>
        <div className="ops-health-grid">
          <article className="ops-health-card">
            <h3>容量健康</h3>
            <QueryState label="容量健康" pending={capacity.isPending} error={capacity.error} onRetry={() => void capacity.refetch()} />
            {capacity.data && (
              <>
                <strong className={`ops-status ops-status--${capacity.data.status ?? 'unknown'}`}>{display(capacity.data.status ?? '未知')}</strong>
                {Object.entries(capacityChecks).map(([name, status]) => <p key={name}><span>{name}</span><b>{display(status)}</b></p>)}
                {capacity.data.reason && <small>{display(capacity.data.reason)}</small>}
              </>
            )}
          </article>
          <article className="ops-health-card">
            <h3>Readiness</h3>
            <QueryState label="Readiness" pending={readiness.isPending} error={readiness.error} onRetry={() => void readiness.refetch()} />
            {readiness.data && (
              <>
                <strong className={`ops-status ops-status--${readiness.data.status ?? 'unknown'}`}>{display(readiness.data.status ?? '未知')}</strong>
                {Object.entries(readinessChecks).map(([name, status]) => <p key={name}><span>{name}</span><b>{display(status)}</b></p>)}
                {readiness.data.reason && <small>{display(readiness.data.reason)}</small>}
              </>
            )}
          </article>
        </div>
      </section>

      <section className="ops-panel">
        <div className="ops-panel-heading">
          <div><h2>Scheduler 调度状态</h2><p>只读展示作业配置与最近执行记录</p></div>
        </div>
        <div className="ops-scheduler-summary">
          <span>作业 <b>{jobs.isError ? '不可用' : jobRows.length}</b></span>
          <span>最近执行 <b>{runs.isError ? '不可用' : runRows.length}</b></span>
          <span>累计失败 <b>{jobs.isError ? '—' : jobRows.reduce((total, job) => total + (Number(job.failure_count) || 0), 0)}</b></span>
        </div>
        <div className="ops-scheduler-data">
          <section className="ops-scheduler-block" aria-label="Scheduler 作业列表">
            <h3>作业列表</h3>
            <QueryState label="Scheduler 作业列表" pending={jobs.isPending} error={jobs.error} onRetry={() => void jobs.refetch()} />
            {!jobs.isError && jobs.data !== undefined && (jobRows.length ? (
              <div className="ops-table-wrap"><table><thead><tr><th>Job</th><th>启用</th><th>状态</th><th>失败次数</th><th>最近结果 / 错误</th><th>间隔</th></tr></thead>
                <tbody>{jobRows.map((job, index) => <tr key={job.job_id ?? String(index)}>
                  <td>{display(job.name ?? job.job_id)}</td><td>{display(job.enabled)}</td>
                  <td><span className={statusClass(job.status)}>{display(job.status ?? '未知')}</span></td>
                  <td>{display(job.failure_count ?? 0)}</td><td>{display(job.last_message ?? job.last_result)}</td>
                  <td>{job.interval_seconds ? `${display(job.interval_seconds)} 秒` : '—'}</td>
                </tr>)}</tbody></table></div>
            ) : <p className="ops-muted">Scheduler API 已响应，但作业列表为空。</p>)}
          </section>
          <section className="ops-scheduler-block" aria-label="Scheduler 最近执行记录">
            <h3>最近执行记录</h3>
            <QueryState label="Scheduler 执行记录" pending={runs.isPending} error={runs.error} onRetry={() => void runs.refetch()} />
            {!runs.isError && runs.data !== undefined && (runRows.length ? (
              <div className="ops-table-wrap"><table><thead><tr><th>Run</th><th>Job</th><th>状态</th><th>开始时间</th><th>结束时间</th><th>失败详情</th></tr></thead>
                <tbody>{runRows.map((run, index) => <tr key={run.run_id ?? String(index)}>
                  <td>{display(run.run_id)}</td><td>{display(run.job_id)}</td>
                  <td><span className={statusClass(run.status)}>{display(run.status ?? '未知')}</span></td>
                  <td>{timestamp(run.started_at)}</td><td>{timestamp(run.finished_at)}</td><td>{display(run.message)}</td>
                </tr>)}</tbody></table></div>
            ) : <p className="ops-muted">Scheduler API 已响应，但最近执行记录为空。</p>)}
          </section>
        </div>
        {(jobs.isError || runs.isError) && <p className="ops-muted">各 Scheduler 数据源独立请求：一项失败不会隐藏另一项成功数据，也不会将失败请求伪装为空列表。</p>}
      </section>
    </main>
  )
}
