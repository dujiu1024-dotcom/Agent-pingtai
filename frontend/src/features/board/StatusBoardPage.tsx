import { useMemo } from 'react'
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
  ['agent_management', 'Agent 管理'],
  ['knowledge', '知识库'],
  ['rag', 'RAG 检索'],
  ['memory', '记忆'],
  ['sessions', '会话'],
  ['approvals', '审批'],
  ['evaluation_runs', '评测运行'],
  ['batch_execution', '批量执行'],
  ['blackboard', '协作黑板'],
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

function isToday(value?: string): boolean {
  if (!value) return false
  const date = new Date(value)
  const now = new Date()
  return (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  )
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
        tone: capabilitiesQuery.isError ? 'down' : 'warn',
        detail: capabilitiesQuery.isError
          ? errorText(capabilitiesQuery.error)
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
      tone: tasksQuery.isSuccess ? 'ok' : tasksQuery.isError ? 'down' : 'warn',
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
  const doneToday = tasks.filter(
    (task) =>
      ['succeeded', 'completed'].includes(task.status ?? '') &&
      isToday(task.finished_at ?? task.updated_at),
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
  const overall: { tone: Tone; title: string; summary: string } =
    liveQuery.isPending || capabilitiesQuery.isPending
      ? { tone: 'warn', title: '正在检测', summary: '首次探测尚未完成。' }
      : downCount > 0
        ? {
            tone: 'down',
            title: `${downCount} 项服务异常`,
            summary: '请检查下方标红项，任务提交可能受影响。',
          }
        : {
            tone: 'ok',
            title: '全部正常',
            summary: '接口与已开启的能力均可用，可以正常提交任务。',
          }

  const updatedAt = Math.max(
    liveQuery.dataUpdatedAt,
    readyQuery.dataUpdatedAt,
    capabilitiesQuery.dataUpdatedAt,
    tasksQuery.dataUpdatedAt,
    deadLetterQuery.dataUpdatedAt,
  )
  const refreshAll = () => {
    void liveQuery.refetch()
    void readyQuery.refetch()
    void capabilitiesQuery.refetch()
    void tasksQuery.refetch()
    void deadLetterQuery.refetch()
  }

  return (
    <section className="page-content status-board">
      <div className="page-heading board-heading">
        <div>
          <p className="eyebrow">工作空间 / 运行看板</p>
          <h1>运行看板</h1>
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
        <article className="board-kpi">
          <span>运行中</span>
          <strong>{runningCount}</strong>
          <small>正在执行的运行</small>
        </article>
        <article className="board-kpi">
          <span>排队中</span>
          <strong>{queuedCount}</strong>
          <small>等待调度</small>
        </article>
        <article className="board-kpi board-kpi--good">
          <span>今日完成</span>
          <strong>{doneToday}</strong>
          <small>当天成功结束</small>
        </article>
        <article className="board-kpi board-kpi--bad">
          <span>异常</span>
          <strong>{failedTasks.length + deadLetters.length}</strong>
          <small>
            失败 {failedTasks.length} · 死信 {deadLetters.length}
          </small>
        </article>
      </div>

      <div className="board-grid">
        <section className="board-panel">
          <header>
            <h2>服务状态</h2>
            <small>
              正常 {services.filter((row) => row.tone === 'ok').length} · 异常{' '}
              {downCount} · 未接入{' '}
              {services.filter((row) => row.tone === 'off').length}
            </small>
          </header>
          <ul className="board-services">
            {services.map((row) => (
              <li key={row.name} className={`row--${row.tone}`}>
                <span className="board-dot" aria-hidden="true" />
                <strong>{row.name}</strong>
                <small>{row.detail}</small>
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

      <p className="board-footnote">
        灰色项表示该能力未接入或未配置，不代表故障；红色才表示探测失败。数据每
        10 秒自动刷新。
      </p>
    </section>
  )
}
