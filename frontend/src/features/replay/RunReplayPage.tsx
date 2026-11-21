import { useState } from 'react'
import type { FormEvent } from 'react'

import { apiClient } from '@/lib/api'
import './RunReplayPage.css'

type TraceEvent = {
  sequence: number
  event_type: string
  occurred_at: string | null
  step_id: string | null
  payload: Record<string, unknown>
}

type ReplayState = {
  session_id: string
  run_id: string
  cursor: number
  total: number
  paused: boolean
  event: TraceEvent | null
  context: Record<string, unknown>
}

type EventPage = { items: TraceEvent[]; total: number }

const errorText = (error: unknown) => {
  const message = error instanceof Error ? error.message : ''
  if (message.includes('context snapshot is missing')) {
    return '该 Run 没有持久化上下文快照，无法回放（409）。请确认 Run 由当前版本执行器写入。'
  }
  if (message.includes('required replay fields')) {
    return `上下文快照缺少回放必需字段：${message}`
  }
  if (message.includes('permission') || message.includes('denied')) {
    return '当前账号没有该 Run 的回放权限。'
  }
  return message || '回放请求失败，请稍后重试。'
}

export function RunReplayPage() {
  const [runId, setRunId] = useState('')
  const [state, setState] = useState<ReplayState | null>(null)
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [sequence, setSequence] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const base = state ? `/platform/v1/runs/${state.run_id}/replay` : ''

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setBusy(false)
    }
  }

  const open = (event: FormEvent) => {
    event.preventDefault()
    const id = runId.trim()
    if (!id) {
      setError('请填写 Run ID。')
      return
    }
    void run(async () => {
      const next = await apiClient.post<ReplayState>(
        `/platform/v1/runs/${encodeURIComponent(id)}/replay`,
      )
      setState(next)
      const page = await apiClient.get<EventPage>(
        `/platform/v1/runs/${encodeURIComponent(id)}/replay/${next.session_id}/events?limit=500`,
      )
      setEvents(page.items)
    })
  }

  const control = (name: string, body?: Record<string, unknown>) =>
    run(async () => {
      if (!state) return
      const next = await apiClient.post<ReplayState>(`${base}/${state.session_id}/${name}`, body ?? {})
      setState(next)
    })

  const seek = () => {
    const value = Number(sequence)
    if (!Number.isInteger(value) || value < 0) {
      setError('请输入非负整数序号。')
      return
    }
    void control('seek', { sequence: value })
  }

  const context = state?.context ?? {}
  const current = state?.event

  return (
    <section className="page-content replay-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">任务中心 / 运行回放</p>
          <h1>运行回放</h1>
          <p>读取持久化上下文快照与 trace 时间线，逐步回放单个 Run 的执行过程。</p>
        </div>
      </div>

      <section className="replay-card">
        <form className="replay-open" onSubmit={open}>
          <label>
            Run ID
            <input
              value={runId}
              onChange={(event) => setRunId(event.target.value)}
              placeholder="填写 Run ID"
            />
          </label>
          <button className="button-primary" type="submit" disabled={busy}>
            {busy ? '打开中…' : '打开回放'}
          </button>
        </form>
        {error && <p className="replay-error" role="alert">{error}</p>}
      </section>

      {state && (
        <>
          <section className="replay-card">
            <header className="replay-header">
              <div>
                <h2>会话状态</h2>
                <p>
                  进度 {state.cursor} / {state.total}，{state.paused ? '已暂停' : '播放中'}
                </p>
              </div>
              <div className="replay-actions">
                <button type="button" onClick={() => void control('step')} disabled={busy}>单步</button>
                <button type="button" onClick={() => void control('back')} disabled={busy}>上一步</button>
                <button type="button" onClick={() => void control('pause')} disabled={busy || state.paused}>暂停</button>
                <button type="button" onClick={() => void control('resume')} disabled={busy || !state.paused}>恢复</button>
                <button type="button" onClick={() => void control('fast_forward')} disabled={busy}>快进到底</button>
              </div>
            </header>

            <div className="replay-seek">
              <label>
                跳转到序号
                <input
                  value={sequence}
                  onChange={(event) => setSequence(event.target.value)}
                  placeholder="例如 12"
                  inputMode="numeric"
                />
              </label>
              <button type="button" onClick={seek} disabled={busy}>跳转</button>
              <button type="button" onClick={() => void control('rerun', { sequence: Number(sequence) || 0 })} disabled={busy}>
                从序号重跑
              </button>
            </div>

            <dl className="replay-context">
              <div><dt>阶段</dt><dd>{String(context.phase ?? '—')}</dd></div>
              <div><dt>版本</dt><dd>{String(context.version ?? '—')}</dd></div>
              <div><dt>记忆快照</dt><dd>{String(context.memory_snapshot_ref ?? '—')}</dd></div>
              <div><dt>快照修订</dt><dd>{String(context.snapshot_revision ?? '—')}</dd></div>
            </dl>
          </section>

          <section className="replay-card">
            <header className="replay-header">
              <div>
                <h2>当前事件</h2>
                <p>{current ? `序号 ${current.sequence} · ${current.event_type}` : '尚未单步或时间线为空。'}</p>
              </div>
            </header>
            {current ? (
              <pre className="replay-json">{JSON.stringify(current.payload, null, 2)}</pre>
            ) : (
              <p className="replay-empty">该 Run 暂无持久化 trace 事件。</p>
            )}
          </section>

          <section className="replay-card">
            <header className="replay-header">
              <div>
                <h2>时间线</h2>
                <p>共 {events.length} 条事件（最多读取 500 条）。</p>
              </div>
            </header>
            {events.length === 0 ? (
              <p className="replay-empty">没有可展示的 trace 事件。</p>
            ) : (
              <div className="replay-table-wrap">
                <table className="replay-table">
                  <thead>
                    <tr>
                      <th>序号</th>
                      <th>类型</th>
                      <th>步骤</th>
                      <th>时间</th>
                      <th>摘要</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((event) => (
                      <tr key={event.sequence}>
                        <td>{event.sequence}</td>
                        <td>{event.event_type}</td>
                        <td>{event.step_id ?? '—'}</td>
                        <td>{event.occurred_at ? new Date(event.occurred_at).toLocaleString() : '—'}</td>
                        <td className="replay-summary">{JSON.stringify(event.payload)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </section>
  )
}
