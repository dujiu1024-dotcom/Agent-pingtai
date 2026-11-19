import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'

import { ApiError, apiClient } from '@/lib/api'
import './status-board.css'

type FeatureMap = Record<string, boolean>

type TaskRecord = {
  task_id: string
  run_id?: string
  agent_id?: string
  status?: string
  created_at?: string
  started_at?: string
  finished_at?: string
  updated_at?: string
  last_error?: string | null
  input?: { message?: string; [key: string]: unknown }
}

type DeadLetter = {
  run_id: string
  task_id?: string | null
  agent_id?: string
  attempt_count?: number
  max_attempts?: number
  reason?: string | null
  created_at?: string
}

type ProbeResult = {
  ok: boolean
  status: number
  body: Record<string, unknown> | null
}

type Tone = 'ok' | 'warn' | 'down' | 'off'

type ServiceRow = { name: string; tone: Tone; detail: string }
type ServiceFilter = 'all' | Tone

const tasksPath = '/platform/v1/workbench/tasks'

const statusLabels: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  paused: '已暂停',
  cancelling: '取消中',
  succeeded: '已完成',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timed_out: '超时',
  interrupted: '已中断',
}

const featureLabels: Array<[key: string, label: string]> = [
  ['task_execution', '任务执行'],
  ['agent_management', '智能体管理'],
  ['knowledge', '知识库'],
  ['rag', '知识检索'],
  ['memory', '记忆'],
  ['sessions', '会话'],
  ['approvals', '审批'],
  ['evaluation_runs', '评测运行'],
  ['batch_execution', '批量执行'],
  ['blackboard', '协作数据板'],
  ['metrics', '指标导出'],
  ['audit', '审计'],
]

async function probe(path: string): Promise<ProbeResult> {
  const response = await fetch(path, {
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  let body: Record<string, unknown> | null = null
  try {
    const parsed: unknown = await response.json()
    body =
      parsed && typeof parsed === 'object'
        ? (parsed as Record<string, unknown>)
        : null
  } catch {
    body = null
  }
  return { ok: response.ok, status: response.status, body }
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : '请求失败'
}

function elapsed(from?: string): string {
  if (!from) return '—'
  const started = Date.parse(from)
  if (!Number.isFinite(started)) return '—'
  const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000))
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function timeOf(value?: string): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString()
}

function taskLabel(task: TaskRecord): string {
  const message = task.input?.message
  if (typeof message === 'string' && message.trim()) return message.trim()
  return `${task.agent_id ?? '未指定 Agent'} · ${task.task_id}`
}

function isActive(status: string): boolean {
  return ['queued', 'running', 'paused', 'cancelling', 'pending'].includes(
    status,
  )
}

function isFailed(status: string): boolean {
  return ['failed', 'timed_out', 'interrupted'].includes(status)
}

export function StatusBoardPage() {
  const [serviceFilter, setServiceFilter] = useState<ServiceFilter>('all')
  const liveQuery = useQuery({
    queryKey: ['board', 'live'],
    queryFn: () => probe('/health/live'),
    refetchInterval: 10_000,
    retry: false,
  })
  const readyQuery = useQuery({
    queryKey: ['board', 'ready'],
    queryFn: () => probe('/health/ready'),
    refetchInterval: 10_000,
    retry: false,
  })
  const capabilitiesQuery = useQuery({
    queryKey: ['board', 'capabilities'],
    queryFn: () =>
      apiClient.get<{ features?: FeatureMap }>(
        '/platform/v1/workbench/capabilities',
      ),
    refetchInterval: 10_000,
    retry: false,
  })
  const capacityQuery = useQuery({
    queryKey: ['board', 'capacity'],
    queryFn: () =>
      apiClient.get<{
        status?: string
        capacity?: Record<string, unknown>
      }>('/platform/v1/observability/health/capacity'),
    refetchInterval: 30_000,
    retry: false,
  })
  const tasksQuery = useQuery({
    queryKey: ['board', 'tasks'],
    queryFn: () => apiClient.get<{ items?: TaskRecord[] }>(tasksPath),
    refetchInterval: 10_000,
    retry: false,
  })
  const deadLetterQuery = useQuery({
    queryKey: ['board', 'dead-letters'],
    queryFn: () =>
      apiClient.get<{ items?: DeadLetter[] }>(
        '/platform/v1/workbench/dead-letters',
      ),
    refetchInterval: 10_000,
    retry: false,
  })

  const services = useMemo<ServiceRow[]>(() => {
    const rows: ServiceRow[] = []
    const live = liveQuery.data
    rows.push({
      name: '接口服务',
      tone: liveQuery.isPending ? 'warn' : live?.ok ? 'ok' : 'down',
      detail: live
        ? live.ok
          ? 'health/live 正常'
          : `health/live 返回 ${live.status}`
        : liveQuery.isError
          ? '探针不可达'
          : '检测中…',
    })

    const ready = readyQuery.data
    if (!ready) {
      rows.push({
        name: '依赖就绪',
        tone: readyQuery.isError ? 'down' : 'warn',
        detail: readyQuery.isError ? '探针不可达' : '检测中…',
      })
    } else {
      const checks = (ready.body?.checks ?? {}) as Record<string, string>
      const names = Object.keys(checks)
      const failed = names.filter((name) => checks[name] !== 'ok')
      const unconfigured =
        ready.status === 503 &&
        String(ready.body?.reason ?? '').includes('not configured')
      rows.push({
        name: '依赖就绪',
        tone: unconfigured ? 'off' : ready.ok ? 'ok' : 'down',
        detail: unconfigured
          ? '未配置就绪检查项'
          : names.length === 0
            ? '无检查项'
            : failed.length === 0
              ? `${names.length} 项依赖全部正常`
              : `异常：${failed.join('、')}`,
      })
    }

    const features = capabilitiesQuery.data?.features
    if (!features) {
      rows.push({
        name: '能力清单',
        tone: 'warn',
        detail: capabilitiesQuery.isError
          ? `能力清单不可读：${errorText(capabilitiesQuery.error)}`
          : '读取中…',
      })
    } else {
      for (const [key, label] of featureLabels) {
        if (!(key in features)) continue
        rows.push({
          name: label,
          tone: features[key] ? 'ok' : 'off',
          detail: features[key] ? '已开启' : '未接入',
        })
      }
    }

    rows.push({
      name: '任务中心',
      tone: tasksQuery.isSuccess ? 'ok' : 'warn',
      detail: tasksQuery.isSuccess
        ? '任务列表可读取'
        : tasksQuery.isError
          ? errorText(tasksQuery.error)
          : '读取中…',
    })
    rows.push({
      name: '死信队列',
      tone: deadLetterQuery.isSuccess
        ? (deadLetterQuery.data?.items?.length ?? 0) > 0
          ? 'warn'
          : 'ok'
        : deadLetterQuery.isError
          ? 'off'
          : 'warn',
      detail: deadLetterQuery.isSuccess
        ? `${deadLetterQuery.data?.items?.length ?? 0} 条未处理`
        : deadLetterQuery.isError
          ? '未配置或不可读'
          : '读取中…',
    })
    const capacityStatus = capacityQuery.data?.status
    rows.push({
      name: '容量监控',
      tone:
        capacityStatus === 'ok'
          ? 'ok'
          : capacityStatus === 'critical'
            ? 'down'
            : capacityStatus === 'warning'
              ? 'warn'
              : 'off',
      detail: capacityStatus
        ? `容量检查状态：${capacityStatus}`
        : capacityQuery.isError
          ? '容量接口不可读'
          : '尚未取得容量数据',
    })

    // These subsystems have no independently verified probe in the current
    // frontend contract. Keep them visible as unconfigured instead of green.
    const notIndividuallyProbed = [
      'PostgreSQL 数据库',
      'Redis 缓存',
      '消息队列',
      'Worker 执行器',
      '调度器',
      '执行队列',
      '失败重试积压',
      '消息待确认（Pending）',
      '事务消息积压（Outbox）',
      '执行租约（Lease）',
      '指标采集（Prometheus）',
      '告警检测',
      '告警通知通道',
    ]
    for (const name of notIndividuallyProbed) {
      if (!rows.some((row) => row.name === name)) {
        rows.push({
          name,
          tone: 'off',
          detail: '尚未接入独立监控探针，无法判断状态',
        })
      }
    }
    return rows
  }, [
    liveQuery.data,
    liveQuery.isPending,
    liveQuery.isError,
    readyQuery.data,
    readyQuery.isError,
    capabilitiesQuery.data,
    capabilitiesQuery.isError,
    capabilitiesQuery.error,
    capacityQuery.data,
    capacityQuery.isError,
    tasksQuery.isSuccess,
    tasksQuery.isError,
    tasksQuery.error,
    deadLetterQuery.isSuccess,
    deadLetterQuery.isError,
    deadLetterQuery.data,
  ])

  const tasks = tasksQuery.data?.items ?? []
  const deadLetters = deadLetterQuery.data?.items ?? []
  const activeTasks = tasks
    .filter((task) => isActive(task.status ?? ''))
    .sort(
      (a, b) =>
        (Date.parse(a.started_at ?? a.created_at ?? '') || 0) -
        (Date.parse(b.started_at ?? b.created_at ?? '') || 0),
    )
    .slice(0, 6)
  const runningCount = tasks.filter((task) => task.status === 'running').length
  const queuedCount = tasks.filter((task) =>
    ['queued', 'pending'].includes(task.status ?? ''),
  ).length
  const failedTasks = tasks
    .filter((task) => isFailed(task.status ?? ''))
    .sort(
      (a, b) =>
        (Date.parse(b.updated_at ?? b.finished_at ?? '') || 0) -
        (Date.parse(a.updated_at ?? a.finished_at ?? '') || 0),
    )
    .slice(0, 5)

  const downCount = services.filter((row) => row.tone === 'down').length
  const okCount = services.filter((row) => row.tone === 'ok').length
  const warnCount = services.filter((row) => row.tone === 'warn').length
  const offCount = services.filter((row) => row.tone === 'off').length
  const confirmedProbe = Boolean(liveQuery.data || readyQuery.data)
  const overall: { tone: Tone; title: string; summary: string } =
    liveQuery.isPending || readyQuery.isPending || capabilitiesQuery.isPending
      ? {
          tone: 'warn',
          title: '正在检测',
          summary: '正在读取服务探针；检测完成前不判断为正常。',
        }
      : downCount > 0
        ? {
            tone: 'down',
            title: `${downCount} 项服务异常`,
            summary: '请优先处理下方异常项。',
          }
        : !confirmedProbe || warnCount > 0 || offCount > 0
          ? {
              tone: 'warn',
              title: '监控数据不完整',
              summary: '部分服务尚未接入或无法检测，暂时不能确认平台整体健康。',
            }
          : {
              tone: 'ok',
              title: '已检测服务正常',
              summary: '仅代表已接入并完成探测的服务，不代表未接入组件正常。',
            }

  const updatedAt = Math.max(
    liveQuery.dataUpdatedAt,
    readyQuery.dataUpdatedAt,
    capabilitiesQuery.dataUpdatedAt,
    capacityQuery.dataUpdatedAt,
    tasksQuery.dataUpdatedAt,
    deadLetterQuery.dataUpdatedAt,
  )
  const refreshAll = () => {
    void liveQuery.refetch()
    void readyQuery.refetch()
    void capabilitiesQuery.refetch()
    void capacityQuery.refetch()
    void tasksQuery.refetch()
    void deadLetterQuery.refetch()
  }

  return (
    <section className="page-content status-board">
      <div className="page-heading board-heading">
        <div>
          <p className="eyebrow">平台运行状态</p>
          <h1>运维状态看板</h1>
          <p>服务状态与任务进展，一屏看完。</p>
        </div>
        <div className="board-refresh">
          <span>10 秒自动刷新</span>
          <small>
            {updatedAt
              ? `更新于 ${timeOf(new Date(updatedAt).toISOString())}`
              : '等待首次数据'}
          </small>
          <button type="button" onClick={refreshAll} aria-label="立即刷新">
            <RefreshCw size={14} strokeWidth={2} aria-hidden="true" />
            刷新
          </button>
        </div>
      </div>

      <div className={`board-hero board-hero--${overall.tone}`}>
        <span className="board-hero-dot" aria-hidden="true" />
        <div>
          <strong>{overall.title}</strong>
          <small>{overall.summary}</small>
        </div>
      </div>

      <div className="board-kpis">
        <article className="board-kpi board-kpi--good">
          <span>正常服务</span>
          <strong>{okCount}</strong>
          <small>已完成检查且正常</small>
        </article>
        <article className="board-kpi board-kpi--bad">
          <span>异常服务</span>
          <strong>{downCount}</strong>
          <small>探测确认异常</small>
        </article>
        <article className="board-kpi">
          <span>未检测 / 未接入</span>
          <strong>{offCount + warnCount}</strong>
          <small>无法据此判断健康</small>
        </article>
        <article className="board-kpi">
          <span>待处理告警</span>
          <strong>—</strong>
          <small>告警接口尚未接入</small>
        </article>
      </div>
      <section className="board-panel board-priority" aria-live="polite">
        <header>
          <h2>当前需要优先处理</h2>
          <small>
            {downCount ? `${downCount} 项已确认异常` : '按已取得的数据判断'}
          </small>
        </header>
        {downCount > 0 ? (
          <ul className="board-priority-list">
            {services
              .filter((row) => row.tone === 'down')
              .map((row) => (
                <li key={row.name}>
                  <strong>{row.name}</strong>
                  <span>{row.detail}</span>
                  <small>
                    建议：查看对应服务日志与健康检查详情；确认依赖恢复后重新探测。
                  </small>
                </li>
              ))}
          </ul>
        ) : failedTasks.length > 0 || deadLetters.length > 0 ? (
          <ul className="board-priority-list">
            {failedTasks.map((task) => (
              <li key={task.task_id}>
                <strong>任务失败：{taskLabel(task)}</strong>
                <span>{task.last_error ?? '任务执行失败，需查看任务详情。'}</span>
                <small>
                  <Link to={`/operations/tasks/${encodeURIComponent(task.task_id)}`}>
                    查看任务详情
                  </Link>
                </small>
              </li>
            ))}
            {deadLetters.map((item) => (
              <li key={item.run_id}>
                <strong>存在未处理的死信消息</strong>
                <span>{item.reason ?? '消息进入死信队列，等待人工检查。'}</span>
                <small>
                  执行标识：{item.run_id} · 重试 {item.attempt_count ?? 0}/
                  {item.max_attempts ?? 0}
                </small>
              </li>
            ))}
          </ul>
        ) : !confirmedProbe || offCount + warnCount > 0 ? (
          <p className="board-muted">
            监控数据尚未完整接入，暂时无法判断平台整体健康状态。请优先补齐未接入服务的探针。
          </p>
        ) : (
          <p className="board-muted">当前没有发现已确认的异常。</p>
        )}
      </section>

      <div className="board-grid">
        <section className="board-panel">
          <header>
            <h2>服务状态</h2>
            <small>
              正常 {okCount} · 异常 {downCount} · 警告 {warnCount} · 未接入{' '}
              {offCount}
            </small>
          </header>
          <nav className="board-filters" aria-label="服务状态筛选">
            {(
              [
                { value: 'all', label: '全部服务' },
                { value: 'ok', label: '正常' },
                { value: 'down', label: '异常' },
                { value: 'warn', label: '警告' },
                { value: 'off', label: '未检测 / 未接入' },
              ] as const
            ).map((item) => (
              <button
                key={item.value}
                type="button"
                aria-pressed={serviceFilter === item.value}
                className={serviceFilter === item.value ? 'active' : ''}
                onClick={() => setServiceFilter(item.value)}
              >
                {item.label}
              </button>
            ))}
          </nav>
          <ul className="board-services">
            {services
              .filter(
                (row) =>
                  serviceFilter === 'all' || row.tone === serviceFilter,
              )
              .map((row) => (
                <li key={row.name} className={`row--${row.tone}`}>
                  <span className="board-dot" aria-hidden="true" />
                  <strong>{row.name}</strong>
                  <small>{row.detail}</small>
                  <em className={`board-status-label status--${row.tone}`}>
                    {{
                      ok: '正常',
                      down: '异常',
                      warn: '警告',
                      off: '尚未接入',
                    }[row.tone]}
                  </em>
                </li>
              ))}
          </ul>
        </section>

        <section className="board-panel">
          <header>
            <h2>正在处理</h2>
            <Link className="text-link" to="/workspace/tasks">
              任务中心 →
            </Link>
          </header>
          {tasksQuery.isPending ? (
            <p className="board-muted">读取中…</p>
          ) : tasksQuery.isError ? (
            <p className="board-muted board-muted--error">
              任务列表不可读：{errorText(tasksQuery.error)}
            </p>
          ) : activeTasks.length === 0 ? (
            <p className="board-muted">当前没有运行中或排队中的任务。</p>
          ) : (
            <ul className="board-tasks">
              {activeTasks.map((task) => (
                <li key={task.task_id}>
                  <span
                    className={`board-dot dot--${task.status ?? 'unknown'}`}
                    aria-hidden="true"
                  />
                  <div>
                    <Link
                      className="board-task-title"
                      to={`/operations/tasks/${encodeURIComponent(task.task_id)}`}
                    >
                      {taskLabel(task)}
                    </Link>
                    <small>
                      {task.agent_id ?? '未指定 Agent'} ·{' '}
                      {statusLabels[task.status ?? ''] ?? task.status ?? '未知'}
                    </small>
                  </div>
                  <em>{elapsed(task.started_at ?? task.created_at)}</em>
                </li>
              ))}
            </ul>
          )}

          <header className="board-subhead">
            <h2>最近失败与死信</h2>
            <Link className="text-link" to="/admin/scheduler/jobs">
              调度任务 →
            </Link>
          </header>
          {failedTasks.length === 0 && deadLetters.length === 0 ? (
            <p className="board-muted">没有失败任务或死信。</p>
          ) : (
            <ul className="board-tasks board-tasks--bad">
              {failedTasks.map((task) => (
                <li key={task.task_id}>
                  <span className="board-dot dot--failed" aria-hidden="true" />
                  <div>
                    <Link
                      className="board-task-title"
                      to={`/operations/tasks/${encodeURIComponent(task.task_id)}`}
                    >
                      {taskLabel(task)}
                    </Link>
                    <small>
                      {task.last_error ??
                        statusLabels[task.status ?? ''] ??
                        '失败'}
                    </small>
                  </div>
                  <em>{timeOf(task.finished_at ?? task.updated_at)}</em>
                </li>
              ))}
              {deadLetters.map((item) => (
                <li key={item.run_id}>
                  <span className="board-dot dot--failed" aria-hidden="true" />
                  <div>
                    <strong className="board-task-title">
                      死信 · {item.agent_id ?? item.run_id}
                    </strong>
                    <small>
                      重试 {item.attempt_count ?? 0}/{item.max_attempts ?? 0}
                      {item.reason ? ` · ${item.reason}` : ''}
                    </small>
                  </div>
                  <em>{timeOf(item.created_at)}</em>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="board-panel board-reliability">
        <header>
          <h2>任务执行可靠性</h2>
          <Link className="text-link" to="/workspace/tasks">
            查看任务中心 →
          </Link>
        </header>
        <div className="board-reliability-grid">
          <div>
            <strong>{queuedCount}</strong>
            <span>等待执行任务</span>
          </div>
          <div>
            <strong>{runningCount}</strong>
            <span>正在执行任务</span>
          </div>
          <div>
            <strong>
              {tasks.filter((task) => task.status === 'paused').length}
            </strong>
            <span>已暂停任务</span>
          </div>
          <div>
            <strong>{failedTasks.length}</strong>
            <span>失败任务</span>
          </div>
          <div>
            <strong>—</strong>
            <span>重试积压（尚未接入）</span>
          </div>
          <div>
            <strong>—</strong>
            <span>消息待确认（尚未接入）</span>
          </div>
          <div>
            <strong>{deadLetters.length}</strong>
            <span>死信消息</span>
          </div>
          <div>
            <strong>—</strong>
            <span>事务消息积压（尚未接入）</span>
          </div>
        </div>
      </section>
      <p className="board-footnote">
        绿色仅表示已成功完成探测；红色表示探测确认异常；橙色表示探测失败或数据不完整；
        灰色表示尚未接入或无法检测。服务数据每 10 秒刷新。
      </p>
    </section>
  )
}
