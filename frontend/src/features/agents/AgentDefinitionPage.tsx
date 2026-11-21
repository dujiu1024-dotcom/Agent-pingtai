import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import { resolveNames, useRegistryCaches } from '@/lib/registry-names'
import { valueLabel } from '@/lib/i18n/labels'

interface RegistryItem {
  kind: string
  item_id: string
  version: number
  definition: Record<string, unknown>
  status: string
  created_by: string
  created_at: string
  published_at: string | null
}

interface AgentDefinition {
  name: string
  description: string
  instructions: string
  model_template_ref: string
  tool_refs: string
  skill_refs: string
  workflow_refs: string
}

const EMPTY: AgentDefinition = {
  name: '',
  description: '',
  instructions: '',
  model_template_ref: '',
  tool_refs: '',
  skill_refs: '',
  workflow_refs: '',
}

function splitList(value: unknown): string {
  if (Array.isArray(value)) return value.join(', ')
  return typeof value === 'string' ? value : ''
}

function toDefinition(item: RegistryItem | undefined): AgentDefinition {
  const definition = item?.definition ?? {}
  return {
    name: String(definition.name ?? ''),
    description: String(definition.description ?? ''),
    instructions: String(definition.instructions ?? ''),
    model_template_ref: String(
      definition.model_template_id ?? definition.model_template_ref ?? '',
    ),
    tool_refs: splitList(definition.tool_ids ?? definition.tool_refs),
    skill_refs: splitList(definition.skill_ids ?? definition.skill_refs),
    workflow_refs: splitList(definition.workflow_ids ?? definition.workflow_refs),
  }
}

function toLists(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function AgentDefinitionPage() {
  const { agentId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const listKey = ['registry', 'agent']
  const [newItemId, setNewItemId] = useState('')
  const [form, setForm] = useState<AgentDefinition>(EMPTY)

  const list = useQuery({
    queryKey: listKey,
    queryFn: () =>
      apiClient.get<{ items: RegistryItem[] }>('/platform/v1/registry/agent'),
    retry: false,
  })

  const { toolNames, skillNames, workflowNames, modelTemplateNames } = useRegistryCaches()

  const active = useQuery({
    queryKey: ['registry', 'agent', agentId, 'active'],
    queryFn: () =>
      apiClient.get<RegistryItem>(
        `/platform/v1/registry/agent/${agentId}/active`,
      ),
    enabled: Boolean(agentId),
    retry: false,
  })
  const versions = useQuery({
    queryKey: ['registry', 'agent', agentId, 'versions'],
    queryFn: () =>
      apiClient.get<{ items: RegistryItem[] }>(
        `/platform/v1/registry/agent/${agentId}/versions`,
      ),
    enabled: Boolean(agentId),
    retry: false,
  })

  useEffect(() => {
    if (!agentId) {
      setForm(EMPTY)
      return
    }
    if (active.data) setForm(toDefinition(active.data))
  }, [agentId, active.data])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: listKey })
    void queryClient.invalidateQueries({
      queryKey: ['registry', 'agent', agentId],
    })
  }

  const save = useMutation({
    mutationFn: async (publish: boolean) => {
      if (!agentId) throw new Error('缺少 Agent ID')
      const definition = {
        name: form.name || agentId,
        description: form.description,
        instructions: form.instructions,
        model_template_id: form.model_template_ref,
        tool_ids: toLists(form.tool_refs),
        skill_ids: toLists(form.skill_refs),
        workflow_ids: toLists(form.workflow_refs),
      }
      const created = await apiClient.post<RegistryItem>(
        `/platform/v1/registry/agent/${agentId}/versions`,
        { definition },
      )
      if (publish) {
        await apiClient.post(
          `/platform/v1/registry/agent/${agentId}/versions/${created.version}/publish`,
        )
      }
      return created
    },
    onSuccess: invalidate,
  })

  const publish = useMutation({
    mutationFn: (version: number) =>
      apiClient.post(
        `/platform/v1/registry/agent/${agentId}/versions/${version}/publish`,
      ),
    onSuccess: invalidate,
  })
  const rollback = useMutation({
    mutationFn: (version: number) =>
      apiClient.post(
        `/platform/v1/registry/agent/${agentId}/rollback/${version}`,
      ),
    onSuccess: invalidate,
  })
  const disable = useMutation({
    mutationFn: () =>
      apiClient.post(`/platform/v1/registry/agent/${agentId}/disable`),
    onSuccess: invalidate,
  })

  const mutationError = [save, publish, rollback, disable].find(
    (item) => item.isError,
  )?.error

  if (!agentId) {
    return (
      <section className="page-content">
        <div className="page-heading">
          <p className="eyebrow">注册表</p>
          <h1>Agent 定义</h1>
          <p>Agent 定义由服务端 Registry 管理，保存即产生新版本，发布后才会生效。</p>
        </div>
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            const id = newItemId.trim()
            if (!id) return
            navigate(`/admin/agents/${id}`)
          }}
        >
          <input
            value={newItemId}
            onChange={(event) => setNewItemId(event.target.value)}
            placeholder="新 Agent ID，例如 support-agent"
            aria-label="新 Agent ID"
          />
          <button className="button-primary" type="submit">
            新建 Agent
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
          <p className="detail-empty">当前租户还没有 Agent 定义。</p>
        )}
        {list.data && list.data.items.length > 0 && (
          <div className="generic-table-wrap">
            <table className="generic-table">
              <thead>
                <tr>
                  <th>智能体标识</th>
                  <th>名称</th>
                  <th>工具</th>
                  <th>技能包</th>
                  <th>版本</th>
                  <th>状态</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((item) => {
                  const tools = (item.definition.tool_ids as string[]) ?? []
                  const skills = (item.definition.skill_ids as string[]) ?? []
                  return (
                    <tr key={item.item_id}>
                      <td>
                        <Link to={`/admin/agents/${item.item_id}`}>
                          {item.item_id}
                        </Link>
                      </td>
                      <td>{String(item.definition.name ?? '—')}</td>
                      <td>
                        {tools.length > 0 ? (
                          tools.map((t) => (
                            <span key={t} className="registry-badge registry-badge--tool">
                              {toolNames[t] ?? t}
                            </span>
                          ))
                        ) : (
                          <em className="cell-empty">—</em>
                        )}
                      </td>
                      <td>
                        {skills.length > 0 ? (
                          skills.map((s) => (
                            <span key={s} className="registry-badge registry-badge--skill">
                              {skillNames[s] ?? s}
                            </span>
                          ))
                        ) : (
                          <em className="cell-empty">—</em>
                        )}
                      </td>
                      <td>v{item.version}</td>
                      <td>
                        <span className={`registry-status registry-status--${item.status}`}>
                          {valueLabel(item.status)}
                        </span>
                      </td>
                      <td>{new Date(item.created_at).toLocaleString('zh-CN')}</td>
                    </tr>
                  )
                })}
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
        <p className="eyebrow">注册表 · Agent</p>
        <h1>{active.data?.definition.name ? String(active.data.definition.name) : agentId}</h1>
        <p>Agent ID：{agentId}</p>
        {active.data && (
          <small>
            当前生效版本 v{active.data.version} · {valueLabel(active.data.status)}
          </small>
        )}
      </div>

      {active.isPending && <LoadingState />}
      {active.isError && (
        <ErrorState
          title="Agent 尚未发布"
          description={
            active.error instanceof Error
              ? active.error.message
              : '该 Agent 还没有生效版本，保存并发布后即可使用。'
          }
        />
      )}

      <div className="editor-grid">
        <label className="editor-field">
          <span>名称</span>
          <input
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </label>
        <label className="editor-field">
          <span>默认模型模板引用</span>
          <input
            value={form.model_template_ref}
            onChange={(event) =>
              setForm({ ...form, model_template_ref: event.target.value })
            }
            placeholder="model_template 的 item_id"
          />
          {form.model_template_ref && modelTemplateNames[form.model_template_ref] && (
            <small className="field-hint">
              中文名：{modelTemplateNames[form.model_template_ref]}
            </small>
          )}
        </label>
        <label className="editor-field editor-field--wide">
          <span>描述</span>
          <input
            value={form.description}
            onChange={(event) =>
              setForm({ ...form, description: event.target.value })
            }
          />
        </label>
        <label className="editor-field editor-field--wide">
          <span>指令（Instructions）</span>
          <textarea
            rows={6}
            value={form.instructions}
            onChange={(event) =>
              setForm({ ...form, instructions: event.target.value })
            }
          />
        </label>
        <label className="editor-field editor-field--wide">
          <span>工具引用（逗号分隔）</span>
          <input
            value={form.tool_refs}
            onChange={(event) =>
              setForm({ ...form, tool_refs: event.target.value })
            }
          />
          {form.tool_refs && (
            <small className="field-hint">
              {toLists(form.tool_refs).map((id) => toolNames[id] ?? id).join('、')}
            </small>
          )}
        </label>
        <label className="editor-field editor-field--wide">
          <span>Skill 引用（逗号分隔）</span>
          <input
            value={form.skill_refs}
            onChange={(event) =>
              setForm({ ...form, skill_refs: event.target.value })
            }
          />
          {form.skill_refs && (
            <small className="field-hint">
              {toLists(form.skill_refs).map((id) => skillNames[id] ?? id).join('、')}
            </small>
          )}
        </label>
        <label className="editor-field editor-field--wide">
          <span>工作流引用（逗号分隔）</span>
          <input
            value={form.workflow_refs}
            onChange={(event) =>
              setForm({ ...form, workflow_refs: event.target.value })
            }
          />
          {form.workflow_refs && (
            <small className="field-hint">
              {toLists(form.workflow_refs).map((id) => workflowNames[id] ?? id).join('、')}
            </small>
          )}
        </label>
      </div>

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
