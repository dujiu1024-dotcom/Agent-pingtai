import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import {
  formatDuration,
  sortEvents,
  unwrapList,
  unwrapRecord,
} from './task-model'
import type { RunEvent, RunRecord, TaskRecord } from './task-model'
const TASKS = '/workbench/tasks',
  RUNS = '/workbench/runs'
const labels: Record<string, string> = {
  pending: '待处理',
  queued: '排队中',
  running: '执行中',
  waiting_approval: '等待审批',
  waiting_human: '等待人工',
  completed: '已完成',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  canceled: '已取消',
  paused: '已暂停',
  blocked: '已阻塞',
}
const priority: Record<string, string> = {
  urgent: '紧急',
  high: '高',
  normal: '普通',
  low: '低',
}
const dt = (v?: string) => (v ? new Date(v).toLocaleString() : '—')
const err = (e: unknown) => (e instanceof Error ? e.message : '请求失败')
function Card({ t }: { t: TaskRecord }) {
  return (
    <Link
      className="task-card"
      to={`/operations/tasks/${encodeURIComponent(t.task_id)}`}
    >
      <small>
        {t.task_id} · {priority[t.priority ?? ''] ?? t.priority ?? '未设优先级'}
      </small>
      <strong>{t.title}</strong>
      {t.description && <p>{t.description}</p>}
      <small>
        {t.project_name ?? t.project_id ?? '未关联项目'} ·{' '}
        {t.assigned_agent_id ?? t.owner ?? '未分派'}
      </small>
      {t.blocked_reason && (
        <p className="task-blocked">阻塞：{t.blocked_reason}</p>
      )}
      <small>
        {labels[t.status] ?? t.status} · {t.latest_run_id ?? '暂无 Run'}
      </small>
    </Link>
  )
}
export function TaskCommandCenter() {
  const [sp, setSp] = useSearchParams(),
    [view, setView] = useState<'kanban' | 'list' | 'timeline'>('kanban')
  const q = useQuery({
    queryKey: ['tasks'],
    queryFn: () =>
      apiClient
        .get<unknown>(TASKS)
        .then((v) => ({ items: unwrapList<TaskRecord>(v), raw: v })),
    refetchInterval: 15000,
  })
  const tasks = q.data?.items ?? []
  const statuses = useMemo(
    () => [...new Set(tasks.map((t) => t.status))].sort(),
    [tasks],
  )
  const filtered = tasks.filter(
    (t) =>
      (!sp.get('q') ||
        `${t.title} ${t.task_id} ${t.description ?? ''}`
          .toLowerCase()
          .includes(sp.get('q')!.toLowerCase())) &&
      (!sp.get('status') || t.status === sp.get('status')) &&
      (!sp.get('priority') || t.priority === sp.get('priority')) &&
      (!sp.get('project_id') || t.project_id === sp.get('project_id')) &&
      (!sp.get('agent_id') || t.assigned_agent_id === sp.get('agent_id')) &&
      (!sp.get('tag') || t.tags?.includes(sp.get('tag')!) === true),
  )
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp)
    if (v) {
      n.set(k, v)
    } else {
      n.delete(k)
    }
    setSp(n)
  }
  return (
    <section className="page-content task-center">
      <div className="page-heading task-heading">
        <div>
          <p className="eyebrow">WORKSPACE / TASK OPERATIONS</p>
          <h1>任务指挥中心</h1>
          <p>跟踪任务生命周期、负责人、阻塞和交付。</p>
        </div>
        <small>自动刷新 · 15s</small>
      </div>
      <div className="task-toolbar">
        <input
          aria-label="搜索任务"
          placeholder="搜索标题 / ID"
          value={sp.get('q') ?? ''}
          onChange={(e) => set('q', e.target.value)}
        />
        <select
          aria-label="状态筛选"
          value={sp.get('status') ?? ''}
          onChange={(e) => set('status', e.target.value)}
        >
          <option value="">全部状态</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {labels[s] ?? s}
            </option>
          ))}
        </select>
        <select
          aria-label="优先级筛选"
          value={sp.get('priority') ?? ''}
          onChange={(e) => set('priority', e.target.value)}
        >
          <option value="">全部优先级</option>
          {Object.entries(priority).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <input
          aria-label="项目筛选"
          placeholder="项目 ID"
          value={sp.get('project_id') ?? ''}
          onChange={(e) => set('project_id', e.target.value)}
        />
        <input
          aria-label="Agent 筛选"
          placeholder="Agent ID"
          value={sp.get('agent_id') ?? ''}
          onChange={(e) => set('agent_id', e.target.value)}
        />
        <div className="task-view-switch">
          {(['kanban', 'list', 'timeline'] as const).map((v) => (
            <button
              className={view === v ? 'selected' : ''}
              key={v}
              onClick={() => setView(v)}
            >
              {v === 'kanban' ? '看板' : v === 'list' ? '列表' : '时间线'}
            </button>
          ))}
        </div>
      </div>
      {q.isPending ? (
        <LoadingState label="正在加载任务" />
      ) : q.isError ? (
        <ErrorState
          title="任务列表加载失败"
          description={err(q.error)}
          onRetry={() => void q.refetch()}
        />
      ) : !filtered.length ? (
        <EmptyState
          title="没有匹配的任务"
          description="调整筛选条件，或等待服务端任务数据。"
        />
      ) : view === 'kanban' ? (
        <div className="task-kanban">
          {statuses.map((s) => (
            <section className="task-column" key={s}>
              <header>
                <strong>{labels[s] ?? s}</strong>
                <span>{filtered.filter((t) => t.status === s).length}</span>
              </header>
              {filtered
                .filter((t) => t.status === s)
                .map((t) => (
                  <Card key={t.task_id} t={t} />
                ))}
            </section>
          ))}
        </div>
      ) : view === 'list' ? (
        <div className="task-table-wrap">
          <table className="task-table">
            <thead>
              <tr>
                <th>任务</th>
                <th>状态</th>
                <th>优先级</th>
                <th>项目</th>
                <th>负责人</th>
                <th>创建时间</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => (
                <tr key={t.task_id}>
                  <td>
                    <Link
                      to={`/operations/tasks/${encodeURIComponent(t.task_id)}`}
                    >
                      {t.title}
                    </Link>
                    <small>{t.task_id}</small>
                  </td>
                  <td>{labels[t.status] ?? t.status}</td>
                  <td>{priority[t.priority ?? ''] ?? t.priority ?? '—'}</td>
                  <td>{t.project_name ?? t.project_id ?? '—'}</td>
                  <td>{t.assigned_agent_id ?? t.owner ?? '—'}</td>
                  <td>{dt(t.created_at)}</td>
                  <td>
                    {t.latest_run_id ? (
                      <Link
                        to={`/operations/runs/${encodeURIComponent(t.latest_run_id)}`}
                      >
                        {t.latest_run_id}
                      </Link>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="task-timeline">
          {filtered
            .filter((t) => t.created_at)
            .sort(
              (a, b) => Date.parse(a.created_at!) - Date.parse(b.created_at!),
            )
            .map((t) => (
              <article key={t.task_id}>
                <time>{dt(t.created_at)}</time>
                <div>
                  <Link
                    to={`/operations/tasks/${encodeURIComponent(t.task_id)}`}
                  >
                    {t.title}
                  </Link>
                  <span>
                    {labels[t.status] ?? t.status} ·{' '}
                    {t.assigned_agent_id ?? '未分派'}
                  </span>
                </div>
              </article>
            ))}
        </div>
      )}
      <p className="task-data-note">
        状态列完全来自服务端枚举；无可信依赖与截止时间字段时不显示
        Gantt。当前不渲染未经核实的写操作。
      </p>
    </section>
  )
}
export function TaskDetailPage() {
  const { taskId = '' } = useParams()
  const tq = useQuery({
    queryKey: ['task', taskId],
    queryFn: () =>
      apiClient
        .get<unknown>(`${TASKS}/${encodeURIComponent(taskId)}`)
        .then(unwrapRecord<TaskRecord>),
  })
  const rq = useQuery({
    queryKey: ['task-runs', taskId],
    queryFn: () =>
      apiClient
        .get<unknown>(`${TASKS}/${encodeURIComponent(taskId)}/runs`)
        .then(unwrapList<RunRecord>),
  })
  if (tq.isPending) return <LoadingState />
  if (tq.isError)
    return (
      <section className="page-content">
        <ErrorState
          title="任务详情加载失败"
          description={err(tq.error)}
          onRetry={() => void tq.refetch()}
        />
      </section>
    )
  const t = tq.data
  return (
    <section className="page-content task-detail">
      <Link to="/workspace/tasks">← 返回任务中心</Link>
      <div className="page-heading">
        <p className="eyebrow">TASK / {t.task_id}</p>
        <h1>{t.title}</h1>
        <p>{t.description ?? '无描述'}</p>
      </div>
      <section className="task-detail-panel">
        <h2>任务信息</h2>
        <dl>
          {[
            ['状态', labels[t.status] ?? t.status],
            ['优先级', priority[t.priority ?? ''] ?? t.priority ?? '—'],
            ['项目', t.project_name ?? t.project_id ?? '—'],
            ['负责人', t.assigned_agent_id ?? t.owner ?? '—'],
            ['创建人', t.created_by ?? '—'],
            ['创建时间', dt(t.created_at)],
            ['更新时间', dt(t.updated_at)],
            ['阻塞原因', t.blocked_reason ?? '—'],
            ['标签', t.tags?.join(', ') ?? '—'],
            ['参与 Agent', t.participant_agent_ids?.join(', ') ?? '—'],
          ].map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        <h3>输入 / 输出</h3>
        <pre>
          {JSON.stringify(
            { input: t.input ?? null, output: t.output ?? null },
            null,
            2,
          )}
        </pre>
      </section>
      <section className="task-detail-panel">
        <h2>Run 列表</h2>
        {rq.isPending ? (
          <LoadingState />
        ) : rq.isError ? (
          <ErrorState
            description={err(rq.error)}
            onRetry={() => void rq.refetch()}
          />
        ) : rq.data?.length ? (
          rq.data.map((r) => (
            <Link
              className="run-row"
              key={r.run_id}
              to={`/operations/runs/${encodeURIComponent(r.run_id)}`}
            >
              {r.run_id} · {r.status} · {dt(r.started_at)} ·{' '}
              {formatDuration(r.duration_ms)} → Trace
            </Link>
          ))
        ) : (
          <EmptyState title="暂无 Run" description="服务端没有返回执行记录。" />
        )}
      </section>
      <section className="task-detail-panel">
        <h2>交付物</h2>
        {t.artifacts?.length ? (
          t.artifacts.map((a, i) => (
            <p key={a.id ?? i}>
              {a.url ? (
                <a href={a.url} target="_blank" rel="noreferrer">
                  {a.name ?? a.id ?? '打开产物'}
                </a>
              ) : (
                (a.name ?? a.id)
              )}
            </p>
          ))
        ) : (
          <p>未返回产物引用。</p>
        )}
      </section>
      <p className="task-data-note">
        不显示未经
        API/权限契约验证的创建、编辑、取消、重试、暂停、恢复或重分派动作。
      </p>
    </section>
  )
}
export function RunTracePage() {
  const { runId = '' } = useParams(),
    nav = useNavigate()
  const rq = useQuery({
    queryKey: ['run', runId],
    queryFn: () =>
      apiClient
        .get<unknown>(`${RUNS}/${encodeURIComponent(runId)}`)
        .then(unwrapRecord<RunRecord>),
  })
  const eq = useQuery({
    queryKey: ['run-events', runId],
    queryFn: () =>
      apiClient
        .get<unknown>(`${RUNS}/${encodeURIComponent(runId)}/events`)
        .then(unwrapList<RunEvent>),
    refetchInterval: 5000,
  })
  if (rq.isPending) return <LoadingState />
  if (rq.isError)
    return (
      <section className="page-content">
        <ErrorState
          title="Run Trace 加载失败"
          description={err(rq.error)}
          onRetry={() => void rq.refetch()}
        />
      </section>
    )
  const r = rq.data,
    events = sortEvents(eq.data ?? [])
  return (
    <section className="page-content task-detail">
      <button className="button-link" onClick={() => nav(-1)}>
        ← 返回
      </button>
      <div className="page-heading">
        <p className="eyebrow">RUN TRACE / {r.run_id}</p>
        <h1>Run 执行时间线</h1>
        <p>
          {r.status} · Trace ID {r.trace_id ?? '未提供'}
        </p>
      </div>
      <section className="task-detail-panel run-summary">
        <div>
          Task{' '}
          <Link to={`/operations/tasks/${encodeURIComponent(r.task_id)}`}>
            {r.task_id}
          </Link>
        </div>
        <div>Agent {r.agent_id ?? '—'}</div>
        <div>开始 {dt(r.started_at)}</div>
        <div>结束 {dt(r.finished_at)}</div>
        <div>耗时 {formatDuration(r.duration_ms)}</div>
        <div>错误码 {r.error_code ?? '—'}</div>
      </section>
      <section className="task-detail-panel">
        <h2>事件时间线</h2>
        {eq.isPending ? (
          <LoadingState />
        ) : eq.isError ? (
          <ErrorState
            description={err(eq.error)}
            onRetry={() => void eq.refetch()}
          />
        ) : events.length ? (
          <ol className="run-timeline">
            {events.map((e) => (
              <li key={e.event_id}>
                <strong>{e.type}</strong>
                <time>{dt(e.occurred_at)}</time>
                <p>{e.summary ?? '无摘要'}</p>
                <small>
                  seq {e.sequence ?? '—'} · {e.actor ?? e.source ?? '未知来源'}{' '}
                  · {e.trace_id ?? r.trace_id ?? '无 Trace'}
                </small>
                {e.payload != null && (
                  <details>
                    <summary>事件载荷</summary>
                    <pre>{JSON.stringify(e.payload, null, 2)}</pre>
                  </details>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <EmptyState
            title="暂无执行事件"
            description="服务端没有返回 Run 事件。"
          />
        )}
      </section>
      <p className="task-data-note">
        事件按 sequence
        优先、时间次之排序；当前未声明服务端游标补偿能力，不承诺断线无损恢复。
      </p>
    </section>
  )
}
