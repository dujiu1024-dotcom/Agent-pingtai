import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  BookOpen,
  Bot,
  CheckCircle2,
  ListTodo,
  Play,
  Workflow,
} from 'lucide-react'

import { ApiError, apiClient } from '@/lib/api'
import { LoadingState } from '@/components/States'
import './quickstart.css'

type AgentItem = {
  item_id: string
  kind: string
  version: number
  status: string
  definition: Record<string, unknown>
}

type TaskRecord = {
  task_id: string
  run_id?: string
  agent_id?: string
  status?: string
  created_at?: string
  updated_at?: string
  last_error?: string | null
  input?: { message?: string; [key: string]: unknown }
}

type SubmitResult = {
  task_id?: string
  run_id?: string
  status?: string
}

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

function agentName(item: AgentItem): string {
  const name = item.definition?.name ?? item.definition?.title
  return typeof name === 'string' && name.trim() ? name : item.item_id
}

function agentSummary(item: AgentItem): string {
  const description = item.definition?.description ?? item.definition?.summary
  return typeof description === 'string' ? description : ''
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return error.requestId
      ? `${error.message}（request_id: ${error.requestId}）`
      : error.message
  }
  return error instanceof Error ? error.message : '请求失败'
}

export function QuickStartPage() {
  const [agentId, setAgentId] = useState('')
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [result, setResult] = useState<SubmitResult | null>(null)

  const capabilitiesQuery = useQuery({
    queryKey: ['quickstart', 'capabilities'],
    queryFn: () =>
      apiClient.get<{ features?: Record<string, boolean> }>(
        '/platform/v1/workbench/capabilities',
      ),
    staleTime: 60_000,
    retry: false,
  })
  const agentsQuery = useQuery({
    queryKey: ['quickstart', 'agents'],
    queryFn: () =>
      apiClient.get<{ items?: AgentItem[] }>('/platform/v1/registry/agent'),
    retry: false,
  })
  const recentQuery = useQuery({
    queryKey: ['quickstart', 'recent-tasks'],
    queryFn: () => apiClient.get<{ items?: TaskRecord[] }>(tasksPath),
    refetchInterval: 10_000,
    retry: false,
  })

  const agents = agentsQuery.data?.items ?? []
  const features = capabilitiesQuery.data?.features ?? {}
  const taskExecutionReady =
    capabilitiesQuery.isSuccess && features.task_execution !== false
  const selected = agents.find((item) => item.item_id === agentId)
  const canSubmit = Boolean(agentId) && message.trim().length > 0 && !submitting

  const recentTasks = useMemo(
    () =>
      [...(recentQuery.data?.items ?? [])]
        .sort(
          (a, b) =>
            (Date.parse(b.created_at ?? '') || 0) -
            (Date.parse(a.created_at ?? '') || 0),
        )
        .slice(0, 8),
    [recentQuery.data],
  )

  async function submit() {
    if (!canSubmit) return
    setSubmitting(true)
    setSubmitError(null)
    setResult(null)
    try {
      const response = await apiClient.post<SubmitResult>(tasksPath, {
        agent_id: agentId,
        input: { message: message.trim() },
      })
      setResult(response)
      setMessage('')
      void recentQuery.refetch()
    } catch (error) {
      setSubmitError(errorText(error))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="page-content quickstart">
      <div className="page-heading">
        <p className="eyebrow">工作空间 / 快速使用</p>
        <h1>快速使用</h1>
        <p>选一个 Agent，写下要做的事，提交后即可在任务中心跟踪结果。</p>
      </div>

      {capabilitiesQuery.isSuccess && !taskExecutionReady && (
        <div className="qs-banner qs-banner--warn" role="status">
          当前实例未开启任务执行能力，提交入口不可用。
        </div>
      )}

      <div className="qs-steps">
        <section className="qs-card">
          <header className="qs-card-head">
            <span className="qs-step">1</span>
            <div>
              <h2>选择 Agent</h2>
              <p>来自 Agent 注册表当前生效版本。</p>
            </div>
          </header>
          {agentsQuery.isPending ? (
            <LoadingState label="正在读取 Agent 列表" />
          ) : agentsQuery.isError ? (
            <div className="qs-note qs-note--error">
              Agent 列表读取失败：{errorText(agentsQuery.error)}
            </div>
          ) : agents.length === 0 ? (
            <div className="qs-note">
              注册表中还没有可用的 Agent。
              <Link className="text-link" to="/admin/agents">
                先去定义 Agent
              </Link>
            </div>
          ) : (
            <>
              <select
                className="qs-select"
                aria-label="选择 Agent"
                value={agentId}
                onChange={(event) => setAgentId(event.target.value)}
              >
                <option value="">请选择 Agent…</option>
                {agents.map((item) => (
                  <option key={item.item_id} value={item.item_id}>
                    {agentName(item)}（{item.item_id} · v{item.version}）
                  </option>
                ))}
              </select>
              {selected && (
                <p className="qs-agent-desc">
                  {agentSummary(selected) || '该 Agent 未填写说明。'}
                </p>
              )}
            </>
          )}
        </section>

        <section className="qs-card">
          <header className="qs-card-head">
            <span className="qs-step">2</span>
            <div>
              <h2>写清要做的事</h2>
              <p>内容会作为消息提交给 Agent。</p>
            </div>
          </header>
          <textarea
            className="qs-textarea"
            aria-label="任务内容"
            rows={5}
            placeholder="例如：整理本周客户问题，输出三条可执行的跟进建议。"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
          />
        </section>

        <section className="qs-card">
          <header className="qs-card-head">
            <span className="qs-step">3</span>
            <div>
              <h2>提交并跟踪</h2>
              <p>提交后生成任务与运行，可进入任务中心查看进度。</p>
            </div>
          </header>
          <button
            className="qs-submit"
            type="button"
            disabled={
              !canSubmit || (capabilitiesQuery.isSuccess && !taskExecutionReady)
            }
            onClick={() => void submit()}
          >
            <Play size={16} strokeWidth={2.2} aria-hidden="true" />
            {submitting ? '提交中…' : '提交任务'}
          </button>
          {submitError && (
            <div className="qs-note qs-note--error" role="alert">
              提交失败：{submitError}
            </div>
          )}
          {result && (
            <div className="qs-result" role="status">
              <CheckCircle2 size={18} strokeWidth={2} aria-hidden="true" />
              <div>
                <strong>已提交</strong>
                <small>
                  任务 {result.task_id ?? '—'} · 状态{' '}
                  {statusLabels[result.status ?? ''] ?? result.status ?? '未知'}
                </small>
              </div>
              {result.task_id && (
                <Link
                  className="text-link"
                  to={`/operations/tasks/${encodeURIComponent(result.task_id)}`}
                >
                  查看任务
                </Link>
              )}
            </div>
          )}
        </section>
      </div>

      <section className="qs-recent">
        <header className="qs-recent-head">
          <h2>最近提交</h2>
          <Link className="text-link" to="/workspace/tasks">
            全部任务 →
          </Link>
        </header>
        {recentQuery.isError ? (
          <div className="qs-note">
            任务列表暂不可用：{errorText(recentQuery.error)}
          </div>
        ) : recentTasks.length === 0 ? (
          <div className="qs-note">
            还没有任务记录，提交第一条后会出现在这里。
          </div>
        ) : (
          <ul className="qs-task-list">
            {recentTasks.map((task) => {
              const status = task.status ?? 'unknown'
              return (
                <li key={task.task_id}>
                  <span
                    className={`qs-dot qs-dot--${status}`}
                    aria-hidden="true"
                  />
                  <Link
                    className="qs-task-title"
                    to={`/operations/tasks/${encodeURIComponent(task.task_id)}`}
                  >
                    {task.input?.message
                      ? String(task.input.message).slice(0, 46)
                      : task.task_id}
                  </Link>
                  <small>{task.agent_id ?? '—'}</small>
                  <span className={`qs-status qs-status--${status}`}>
                    {statusLabels[status] ?? status}
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section className="qs-links" aria-label="常用入口">
        <Link className="qs-link" to="/admin/agents">
          <Bot size={18} strokeWidth={1.9} aria-hidden="true" />
          <span>
            <strong>定义 Agent</strong>
            <small>指令、模型与工具</small>
          </span>
        </Link>
        <Link className="qs-link" to="/admin/workflows">
          <Workflow size={18} strokeWidth={1.9} aria-hidden="true" />
          <span>
            <strong>设计工作流</strong>
            <small>多步骤编排</small>
          </span>
        </Link>
        <Link className="qs-link" to="/operations/knowledge-bases">
          <BookOpen size={18} strokeWidth={1.9} aria-hidden="true" />
          <span>
            <strong>导入知识</strong>
            <small>文档与检索</small>
          </span>
        </Link>
        <Link className="qs-link" to="/workspace/status">
          <ListTodo size={18} strokeWidth={1.9} aria-hidden="true" />
          <span>
            <strong>运行看板</strong>
            <small>服务与任务状态</small>
          </span>
        </Link>
      </section>
    </section>
  )
}
