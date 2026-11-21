import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import { valueLabel } from '@/lib/i18n/labels'
import { WorkflowGraphEditor, type WorkflowEdge, type WorkflowNode } from './WorkflowGraphEditor'
import './workflow-designer.css'

const WORKFLOW_LABELS: Record<string, string> = {
  'agentic-single-turn': 'Agentic 单轮工作流',
  'router-dispatch': '主管路由工作流',
}

const workflowLabel = (itemId: string) =>
  WORKFLOW_LABELS[itemId] ?? itemId

interface RegistryItem {
  kind: string
  item_id: string
  version: number
  definition: Record<string, unknown>
  status: string
  created_at: string
}

interface WorkflowDefinition {
  name: string
  description: string
  nodes: string
  edges: string
}

const EMPTY: WorkflowDefinition = {
  name: '',
  description: '',
  nodes: '[]',
  edges: '[]',
}

function pretty(value: unknown): string {
  return JSON.stringify(value ?? [], null, 2)
}

function toDefinition(item: RegistryItem | undefined): WorkflowDefinition {
  const definition = item?.definition ?? {}
  return {
    name: String(definition.name ?? ''),
    description: String(definition.description ?? ''),
    nodes: pretty(definition.nodes),
    edges: pretty(definition.edges),
  }
}

function safeArray<T>(value: string): T[] {
  try {
    const parsed: unknown = JSON.parse(value || '[]')
    return Array.isArray(parsed) ? parsed as T[] : []
  } catch {
    return []
  }
}

function parseJson(value: string, label: string): unknown {
  try {
    return JSON.parse(value || '[]')
  } catch {
    throw new Error(`${label} 不是合法 JSON`)
  }
}

export function WorkflowDesignerPage() {
  const { workflowId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const listKey = ['registry', 'workflow']
  const [newItemId, setNewItemId] = useState('')
  const [form, setForm] = useState<WorkflowDefinition>(EMPTY)

  const list = useQuery({
    queryKey: listKey,
    queryFn: () =>
      apiClient.get<{ items: RegistryItem[] }>('/platform/v1/registry/workflow'),
    retry: false,
  })
  const active = useQuery({
    queryKey: ['registry', 'workflow', workflowId, 'active'],
    queryFn: () =>
      apiClient.get<RegistryItem>(
        `/platform/v1/registry/workflow/${workflowId}/active`,
      ),
    enabled: Boolean(workflowId),
    retry: false,
  })
  const versions = useQuery({
    queryKey: ['registry', 'workflow', workflowId, 'versions'],
    queryFn: () =>
      apiClient.get<{ items: RegistryItem[] }>(
        `/platform/v1/registry/workflow/${workflowId}/versions`,
      ),
    enabled: Boolean(workflowId),
    retry: false,
  })

  useEffect(() => {
    if (!workflowId) {
      setForm(EMPTY)
      return
    }
    if (active.data) setForm(toDefinition(active.data))
  }, [workflowId, active.data])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: listKey })
    void queryClient.invalidateQueries({
      queryKey: ['registry', 'workflow', workflowId],
    })
  }

  const save = useMutation({
    mutationFn: async (publish: boolean) => {
      if (!workflowId) throw new Error('缺少工作流 ID')
      const definition = {
        name: form.name || workflowId,
        description: form.description,
        nodes: parseJson(form.nodes, '节点'),
        edges: parseJson(form.edges, '连线'),
      }
      const created = await apiClient.post<RegistryItem>(
        `/platform/v1/registry/workflow/${workflowId}/versions`,
        { definition },
      )
      if (publish) {
        await apiClient.post(
          `/platform/v1/registry/workflow/${workflowId}/versions/${created.version}/publish`,
        )
      }
      return created
    },
    onSuccess: invalidate,
  })
  const publish = useMutation({
    mutationFn: (version: number) =>
      apiClient.post(
        `/platform/v1/registry/workflow/${workflowId}/versions/${version}/publish`,
      ),
    onSuccess: invalidate,
  })
  const rollback = useMutation({
    mutationFn: (version: number) =>
      apiClient.post(
        `/platform/v1/registry/workflow/${workflowId}/rollback/${version}`,
      ),
    onSuccess: invalidate,
  })
  const disable = useMutation({
    mutationFn: () =>
      apiClient.post(`/platform/v1/registry/workflow/${workflowId}/disable`),
    onSuccess: invalidate,
  })
  const mutationError = [save, publish, rollback, disable].find(
    (item) => item.isError,
  )?.error

  if (!workflowId) {
    return (
      <section className="page-content">
        <div className="page-heading">
          <p className="eyebrow">注册表</p>
          <h1>工作流设计</h1>
          <p>工作流以 Registry 版本管理，节点与连线随版本一起发布或回滚。</p>
        </div>
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            const input = newItemId.trim()
            const id = input || `workflow-${Date.now()}`
            setNewItemId('')
            navigate(`/admin/workflows/${id}/edit`)
          }}
        >
          <input
            value={newItemId}
            onChange={(event) => setNewItemId(event.target.value)}
            placeholder="新工作流 ID，例如 onboarding-flow"
            aria-label="新工作流 ID"
          />
          <button className="button-primary" type="submit">
            新建工作流
          </button>
        </form>
        {list.isPending && <LoadingState />}
        {list.isError && (
          <ErrorState
            description={
              list.error instanceof Error ? list.error.message : '加载失败'
            }
            onRetry={() => void list.refetch()}
          />
        )}
        {list.data && list.data.items.length === 0 && (
          <p className="detail-empty">当前租户还没有工作流定义。</p>
        )}
        {list.data && list.data.items.length > 0 && (
          <div className="generic-table-wrap">
            <table className="generic-table">
              <thead>
                <tr>
                  <th>工作流 ID</th>
                  <th>名称</th>
                  <th>版本</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((item) => (
                  <tr key={item.item_id}>
                    <td>{workflowLabel(item.item_id)}</td>
                    <td>{String(item.definition.name ?? '—')}</td>
                    <td>v{item.version}</td>
                    <td>{valueLabel(item.status)}</td>
                    <td>
                      <Link
                        className="text-link"
                        to={`/admin/workflows/${item.item_id}/edit`}
                      >
                        编辑
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    )
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">注册表 · 工作流</p>
        <h1>
          {active.data?.definition.name
            ? String(active.data.definition.name)
            : workflowId}
        </h1>
        <p>工作流 ID：{workflowId}</p>
        {active.data && (
          <small>
            当前生效版本 v{active.data.version} · {valueLabel(active.data.status)}
          </small>
        )}
      </div>

      {active.isPending && <LoadingState />}
      {active.isError && (
        <ErrorState
          title="工作流尚未发布"
          description="该工作流还没有生效版本，保存并发布后即可使用。"
        />
      )}

      <WorkflowGraphEditor
        nodes={safeArray<WorkflowNode>(form.nodes)}
        edges={safeArray<WorkflowEdge>(form.edges)}
        onNodesChange={(nodes) => setForm((current) => ({ ...current, nodes: pretty(nodes) }))}
        onEdgesChange={(edges) => setForm((current) => ({ ...current, edges: pretty(edges) }))}
      />

      <details className="workflow-advanced-json"><summary>高级 JSON 定义（可选）</summary><div className="editor-grid">
        <label className="editor-field">
          <span>名称</span>
          <input
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </label>
        <label className="editor-field">
          <span>描述</span>
          <input
            value={form.description}
            onChange={(event) =>
              setForm({ ...form, description: event.target.value })
            }
          />
        </label>
        <label className="editor-field editor-field--wide">
          <span>节点（JSON 数组）</span>
          <textarea
            rows={8}
            value={form.nodes}
            onChange={(event) =>
              setForm({ ...form, nodes: event.target.value })
            }
          />
        </label>
        <label className="editor-field editor-field--wide">
          <span>连线（JSON 数组）</span>
          <textarea
            rows={6}
            value={form.edges}
            onChange={(event) =>
              setForm({ ...form, edges: event.target.value })
            }
          />
        </label>
      </div>

      </div></details>

      <div className="detail-actions">
        <button
          className="button-primary"
          type="button"
          disabled={save.isPending}
          onClick={() => save.mutate(true)}
        >
          保存并发布
        </button>
        <button
          className="button-secondary"
          type="button"
          disabled={save.isPending}
          onClick={() => save.mutate(false)}
        >
          仅保存草稿
        </button>
        <button
          className="button-secondary"
          type="button"
          disabled={disable.isPending}
          onClick={() => disable.mutate()}
        >
          停用
        </button>
        {mutationError && (
          <span className="auth-error" role="alert">
            {mutationError instanceof Error ? mutationError.message : '操作失败'}
          </span>
        )}
      </div>

      <h2 className="detail-section-title">
        版本历史（{versions.data?.items.length ?? 0}）
      </h2>
      {versions.data && versions.data.items.length === 0 && (
        <p className="detail-empty">尚未保存任何版本。</p>
      )}
      {versions.data && versions.data.items.length > 0 && (
        <div className="generic-table-wrap">
          <table className="generic-table">
            <thead>
              <tr>
                <th>版本</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {[...versions.data.items].reverse().map((item) => (
                <tr key={item.version}>
                  <td>v{item.version}</td>
                  <td>{valueLabel(item.status)}</td>
                  <td>{new Date(item.created_at).toLocaleString('zh-CN')}</td>
                  <td className="cell-nowrap">
                    {item.status !== 'published' && (
                      <button
                        className="text-link"
                        type="button"
                        onClick={() => publish.mutate(item.version)}
                      >
                        发布
                      </button>
                    )}
                    {active.data && item.version !== active.data.version && (
                      <button
                        className="text-link"
                        type="button"
                        onClick={() => rollback.mutate(item.version)}
                      >
                        回滚到此版本
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
