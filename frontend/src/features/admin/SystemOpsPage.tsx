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
  [key: string]: unknown
}
type SchedulerRun = {
  run_id?: string
  job_id?: string
  status?: string
  started_at?: string
  finished_at?: string
  [key: string]: unknown
}

const endpoints = {
  metrics: '/platform/v1/observability/metrics/json',
  capacity: '/platform/v1/observability/health/capacity',
  readiness: '/health/ready',
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

function timestamp(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN')
}

function QueryState({ pending, error, onRetry }: {
  pending: boolean
  error: Error | null
  onRetry: () => void
}) {
  if (pending) return <p className="ops-muted">正在读取数据…</p>
  if (!error) return null
  const requestId = 'requestId' in error ? String(error.requestId ?? '') : ''
  return (
    <div className="ops-error" role="alert">
      <span>{error.message}</span>
      {requestId && <small>Request ID: {requestId}</small>}
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
        <QueryState pending={metrics.isPending} error={metrics.error} onRetry={() => void metrics.refetch()} />
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
                    {!value && entries.length > 1 && <small>{entries.length} 个返回字段</small>}
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
            <QueryState pending={capacity.isPending} error={capacity.error} onRetry={() => void capacity.refetch()} />
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
            <QueryState pending={readiness.isPending} error={readiness.error} onRetry={() => void readiness.refetch()} />
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
        <QueryState pending={jobs.isPending || runs.isPending} error={jobs.error ?? runs.error} onRetry={() => { void jobs.refetch(); void runs.refetch() }} />
        {!jobs.isError && !runs.isError && jobs.data !== undefined && runs.data !== undefined && (
          <>
            <div className="ops-scheduler-summary">
              <span>作业 <b>{jobRows.length}</b></span>
              <span>最近执行 <b>{runRows.length}</b></span>
            </div>
            {jobRows.length ? (
              <div className="ops-table-wrap"><table><thead><tr><th>Job</th><th>启用</th><th>状态</th><th>间隔</th></tr></thead>
                <tbody>{jobRows.map((job, index) => <tr key={job.job_id ?? String(index)}>
                  <td>{display(job.name ?? job.job_id)}</td><td>{display(job.enabled)}</td><td>{display(job.status ?? '—')}</td>
                  <td>{job.interval_seconds ? `${display(job.interval_seconds)} 秒` : '—'}</td>
                </tr>)}</tbody></table></div>
            ) : <p className="ops-muted">Scheduler API 返回空作业列表。</p>}
            {runRows.length ? (
              <div className="ops-table-wrap"><table><thead><tr><th>Run</th><th>Job</th><th>状态</th><th>开始时间</th><th>结束时间</th></tr></thead>
                <tbody>{runRows.map((run, index) => <tr key={run.run_id ?? String(index)}>
                  <td>{display(run.run_id)}</td><td>{display(run.job_id)}</td><td>{display(run.status)}</td>
                  <td>{timestamp(run.started_at)}</td><td>{timestamp(run.finished_at)}</td>
                </tr>)}</tbody></table></div>
            ) : <p className="ops-muted">Scheduler API 返回空执行记录。</p>}
          </>
        )}
        {(jobs.isError || runs.isError) && <p className="ops-muted">Scheduler 接口不可用时不展示空列表作为正常状态；请检查后端 Scheduler router/service 注册与权限配置。</p>}
      </section>
    </main>
  )
}
