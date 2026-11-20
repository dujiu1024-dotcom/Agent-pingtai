import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiClient } from '@/lib/api'

type ModelKind = 'model_provider' | 'model_template'

type RegistryItem = {
  kind: string
  item_id: string
  version: number
  definition: Record<string, unknown>
  status: string
  created_by: string | null
  created_at: string
  published_at: string | null
}

type RegistryList = { items: RegistryItem[] }
type RegistryVersions = { items: RegistryItem[] }

const modelSections = [
  { path: '/admin/models/providers', label: '模型供应商', kind: 'model_provider' as const },
  { path: '/admin/models/templates', label: '模型模板', kind: 'model_template' as const },
  { path: '/admin/models/routing', label: '模型路由策略', kind: null },
]

const secretKey = /(^|_)(api_?key|api_?secret|access_?token|refresh_?token|password|secret|secret_?key|token|credential|credentials|client_?secret|private_?key|authorization)$/i

export function parseModelDefinition(raw: string): Record<string, unknown> {
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('配置必须是 JSON 对象')
  }
  const inspect = (node: unknown, path: string) => {
    if (Array.isArray(node)) {
      node.forEach((child, index) => inspect(child, `${path}[${index}]`))
      return
    }
    if (!node || typeof node !== 'object') return
    Object.entries(node).forEach(([key, child]) => {
      const normalized = key.replace(/([a-z0-9])([A-Z])/g, '$1_$2').replace(/-/g, '_')
      if (secretKey.test(normalized)) {
        throw new Error(`配置字段 ${path}.${key} 不允许保存密钥明文，请使用 Secret 引用`)
      }
      inspect(child, `${path}.${key}`)
    })
  }
  inspect(value, 'definition')
  return value as Record<string, unknown>
}

function itemLabel(item: RegistryItem): string {
  const name = item.definition.name ?? item.definition.title ?? item.definition.display_name
  return typeof name === 'string' && name.trim() ? name : item.item_id
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return '当前请求未通过身份认证。请检查实例权限模式与登录状态。'
    if (error.status === 403) return '当前身份没有该模型资源的读取或管理权限。'
    if (error.status === 404) return '资源或 active 版本不存在。'
    return error.message
  }
  return error instanceof Error ? error.message : '模型 Registry 请求失败'
}

export function ModelsManagementPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { providerId, templateId } = useParams<{ providerId: string; templateId: string }>()
  const section = modelSections.find((item) => location.pathname.startsWith(item.path)) ?? modelSections[0]
  const kind: ModelKind | null = section.kind
  const itemId = kind === 'model_provider' ? providerId : kind === 'model_template' ? templateId : undefined
  const [filter, setFilter] = useState('')
  const [draftId, setDraftId] = useState('')
  const [draftJson, setDraftJson] = useState(JSON.stringify({
    name: '',
    provider_type: '',
    endpoint: '',
    model: '',
    timeout_seconds: 30,
    max_retries: 2,
    fallback_model: null,
    secret_ref: '',
  }, null, 2))
  const [formError, setFormError] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['model-registry', kind],
    queryFn: () => apiClient.get<RegistryList>(`/platform/v1/registry/${kind}?limit=100`),
    enabled: Boolean(kind),
    staleTime: 10_000,
    refetchInterval: 30_000,
  })
  const items = listQuery.data?.items ?? []
  const filteredItems = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase()
    if (!needle) return items
    return items.filter((item) =>
      [item.item_id, item.status, itemLabel(item), JSON.stringify(item.definition)]
        .join(' ')
        .toLocaleLowerCase()
        .includes(needle),
    )
  }, [filter, items])

  const activeQuery = useQuery({
    queryKey: ['model-registry-active', kind, itemId],
    queryFn: () => apiClient.get<RegistryItem>(
      `/platform/v1/registry/${kind}/${encodeURIComponent(itemId!)}/active`,
    ),
    enabled: Boolean(kind && itemId),
    retry: false,
  })
  const versionsQuery = useQuery({
    queryKey: ['model-registry-versions', kind, itemId],
    queryFn: () => apiClient.get<RegistryVersions>(
      `/platform/v1/registry/${kind}/${encodeURIComponent(itemId!)}/versions`,
    ),
    enabled: Boolean(kind && itemId),
    retry: false,
  })

  const draftMutation = useMutation({
    mutationFn: ({ id, definition }: { id: string; definition: Record<string, unknown> }) =>
      apiClient.post<RegistryItem>(
        `/platform/v1/registry/${kind}/${encodeURIComponent(id)}/versions`,
        { definition },
      ),
    onSuccess: async (created, variables) => {
      await queryClient.invalidateQueries({ queryKey: ['model-registry', kind] })
      await queryClient.invalidateQueries({ queryKey: ['model-registry-versions', kind, variables.id] })
      navigate(`${section.path}/${encodeURIComponent(variables.id)}`)
      setDraftId('')
      setFormError(null)
      setDraftJson(JSON.stringify(created.definition, null, 2))
    },
    onError: (error) => setFormError(errorText(error)),
  })

  const publishMutation = useMutation({
    mutationFn: (version: number) =>
      apiClient.post<RegistryItem>(
        `/platform/v1/registry/${kind}/${encodeURIComponent(itemId!)}/versions/${version}/publish`,
      ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['model-registry', kind] }),
        queryClient.invalidateQueries({ queryKey: ['model-registry-active', kind, itemId] }),
        queryClient.invalidateQueries({ queryKey: ['model-registry-versions', kind, itemId] }),
      ])
    },
  })

  const handleCreateDraft = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!kind) return
    const id = draftId.trim()
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) {
      setFormError('资源 ID 必须为 1-128 位字母、数字、点、下划线、冒号或连字符。')
      return
    }
    try {
      const definition = parseModelDefinition(draftJson)
      setFormError(null)
      draftMutation.mutate({ id, definition })
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'JSON 配置无效')
    }
  }

  if (!kind) {
    return (
      <section className="page-content">
        <div className="page-heading">
          <p className="eyebrow">PLATFORM MANAGEMENT / MODEL ROUTING</p>
          <h1>模型路由策略</h1>
          <p>路由策略入口保留，但当前后端尚未提供可核验的路由策略 Registry 类型或 CRUD API。</p>
        </div>
        <div className="ops-panel">
          <h2>后端能力缺口</h2>
          <p>当前 Registry 仅声明 model_provider、model_template 等资源类型；没有 routing_policy 类型，也没有 Provider/Template 与模型路由策略之间的可查询绑定接口。</p>
          <p>因此本页不展示虚构策略、不允许提交配置。需要先补齐路由策略的数据模型、租户授权、版本与审计 API，再接入真实页面。</p>
        </div>
      </section>
    )
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">PLATFORM MANAGEMENT / MODEL REGISTRY</p>
        <h1>{section.label}</h1>
        <p>租户隔离的模型资源登记、配置版本与发布状态。密钥只保存 Secret 引用，不接收或回显密钥明文。</p>
      </div>
      <nav className="ops-panel-heading" aria-label="模型管理分类">
        {modelSections.map((item) => (
          <Link
            key={item.path}
            className={item.path === section.path ? 'text-link active' : 'text-link'}
            to={item.path}
            aria-current={item.path === section.path ? 'page' : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>
      <div className="ops-dashboard-grid">
        <section className="ops-panel">
          <div className="ops-panel-heading">
            <h2>{section.label} Registry</h2>
            <span>{items.length} 项 · 30 秒自动刷新</span>
          </div>
          <label className="field-label" htmlFor="model-registry-filter">筛选资源</label>
          <input
            id="model-registry-filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="按名称、资源 ID、状态或配置内容筛选"
          />
          {listQuery.isLoading ? <p className="muted">正在加载模型资源…</p> : null}
          {listQuery.isError ? (
            <div role="alert" className="ops-query-error">
              <span>{errorText(listQuery.error)}</span>
              <button type="button" onClick={() => void listQuery.refetch()}>重试</button>
            </div>
          ) : null}
          {!listQuery.isLoading && !listQuery.isError && filteredItems.length === 0 ? (
            <p className="muted">{items.length ? '没有匹配的资源。' : '当前租户尚无该类模型资源。'}</p>
          ) : null}
          <div className="ops-ranking" role="list">
            {filteredItems.map((item) => (
              <div role="listitem" key={item.item_id} className="ops-panel-heading">
                <div>
                  <strong>{itemLabel(item)}</strong>
                  <small>{item.item_id} · v{item.version} · {item.status}</small>
                </div>
                <Link className="text-link" to={`${section.path}/${encodeURIComponent(item.item_id)}`}>查看详情</Link>
              </div>
            ))}
          </div>
        </section>
        <section className="ops-panel">
          {itemId ? (
            <>
              <div className="ops-panel-heading">
                <h2>资源详情</h2>
                <span>{itemId}</span>
              </div>
              {activeQuery.isLoading ? <p className="muted">正在读取 active 配置…</p> : null}
              {activeQuery.isError ? (
                <div role="alert" className="ops-query-error">
                  <span>{errorText(activeQuery.error)} 当前资源仍可查看版本列表；尚未发布时不会显示 active 配置。</span>
                  <button type="button" onClick={() => void activeQuery.refetch()}>重试</button>
                </div>
              ) : null}
              {activeQuery.data ? (
                <>
                  <p><strong>{itemLabel(activeQuery.data)}</strong></p>
                  <p className="muted">active v{activeQuery.data.version} · {activeQuery.data.status}</p>
                  <dl>
                    <dt>创建者</dt><dd>{activeQuery.data.created_by ?? '未提供'}</dd>
                    <dt>创建时间</dt><dd>{formatDate(activeQuery.data.created_at)}</dd>
                    <dt>发布时间</dt><dd>{formatDate(activeQuery.data.published_at)}</dd>
                  </dl>
                  <h3>当前配置</h3>
                  <pre className="code-preview">{JSON.stringify(activeQuery.data.definition, null, 2)}</pre>
                </>
              ) : null}
              <h3>版本历史</h3>
              {versionsQuery.isLoading ? <p className="muted">正在读取版本…</p> : null}
              {versionsQuery.isError ? <p role="alert">{errorText(versionsQuery.error)}</p> : null}
              {(versionsQuery.data?.items ?? []).map((version) => (
                <div key={version.version} className="ops-panel-heading">
                  <div>
                    <strong>v{version.version} · {version.status}</strong>
                    <small>创建于 {formatDate(version.created_at)} · {version.created_by ?? '创建者未提供'}</small>
                  </div>
                  {version.status === 'draft' ? (
                    <button
                      type="button"
                      disabled={publishMutation.isPending}
                      onClick={() => publishMutation.mutate(version.version)}
                    >
                      {publishMutation.isPending ? '发布中…' : '发布版本'}
                    </button>
                  ) : null}
                </div>
              ))}
              {publishMutation.isError ? <p role="alert">{errorText(publishMutation.error)}</p> : null}
              <Link className="text-link" to={section.path}>返回列表</Link>
            </>
          ) : (
            <>
              <div className="ops-panel-heading"><h2>新建配置版本</h2><span>Draft</span></div>
              <p className="muted">创建操作会写入 Registry 草稿版本；只有显式点击“发布版本”才会成为 active 配置。</p>
              <form onSubmit={handleCreateDraft}>
                <label className="field-label" htmlFor="model-resource-id">资源 ID</label>
                <input
                  id="model-resource-id"
                  value={draftId}
                  onChange={(event) => setDraftId(event.target.value)}
                  placeholder="例如：openai-compatible"
                  required
                />
                <label className="field-label" htmlFor="model-definition">配置 JSON</label>
                <textarea
                  id="model-definition"
                  value={draftJson}
                  onChange={(event) => setDraftJson(event.target.value)}
                  rows={12}
                  spellCheck={false}
                  required
                />
                <p className="muted">可填写 provider_type、endpoint、model、timeout_seconds、max_retries、fallback_model、secret_ref 等声明字段。不要填写 API Key、Token、密码等密钥明文。</p>
                {formError ? <p role="alert">{formError}</p> : null}
                <button type="submit" disabled={draftMutation.isPending}>
                  {draftMutation.isPending ? '保存中…' : '保存为草稿'}
                </button>
              </form>
            </>
          )}
        </section>
      </div>
    </section>
  )
}
