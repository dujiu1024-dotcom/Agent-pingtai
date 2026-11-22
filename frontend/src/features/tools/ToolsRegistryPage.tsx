import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, apiClient } from '@/lib/api'
import './tools-registry-page.css'

type RegistryItem = {
  kind: string
  item_id: string
  version: number
  definition: Record<string, unknown>
  status: string
  created_by?: string
  created_at?: string
  published_at?: string | null
}
type RegistryList = { items?: RegistryItem[] }

const basePath = '/platform/v1/registry/tool'
const display = (value: unknown) => value == null || value === '' ? '—' : String(value)
const errorMessage = (error: unknown) => {
  if (error instanceof ApiError && error.status === 401) return '服务端要求身份认证，请检查当前登录状态。'
  if (error instanceof ApiError && error.status === 403) return '当前身份没有读取工具注册表的权限。'
  if (error instanceof ApiError && error.status === 404) return '工具不存在，或当前身份不可见。'
  return error instanceof Error ? error.message : '读取工具注册表失败。'
}

export function ToolsRegistryPage() {
  const { toolId } = useParams()
  return toolId ? <ToolDetail toolId={toolId} /> : <ToolCatalog />
}

function ToolCatalog() {
  const [items, setItems] = useState<RegistryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await apiClient.get<RegistryList>(basePath + '?limit=100')
      setItems(Array.isArray(response.items) ? response.items : [])
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { void load() }, [load])

  const filtered = items.filter((item) => {
    const text = [item.item_id, item.status, item.definition.name, item.definition.description, item.definition.server_name]
      .map(display).join(' ').toLowerCase()
    return text.includes(query.trim().toLowerCase())
  })
  const servers = new Set(items.map((item) => display(item.definition.server_name)).filter((name) => name !== '—'))

  return <section className="page-content tools-page">
    <header className="tools-heading"><div><p className="eyebrow">UI-G2-805 · TOOL REGISTRY</p><h1>工具与 MCP Server</h1><p>查看服务端已注册的工具定义、版本状态与 MCP Server 元数据。</p></div><button className="button-primary" type="button" onClick={() => void load()} disabled={loading}>{loading ? '刷新中…' : '刷新数据'}</button></header>
    {error && <div className="tools-alert" role="alert">{error}</div>}
    <div className="tools-stats"><article><span>已注册工具</span><strong>{loading ? '—' : items.length}</strong></article><article><span>关联 Server</span><strong>{loading ? '—' : servers.size}</strong></article><article><span>已发布</span><strong>{loading ? '—' : items.filter((item) => item.status === 'published' || item.status === 'active').length}</strong></article></div>
    <section className="tools-panel"><div className="tools-panel-heading"><div><h2>工具目录</h2><p>数据来自 GET /registry/tool；仅展示服务端返回内容。</p></div><label className="tools-filter">筛选<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="名称、ID 或 Server" /></label></div>
      {loading ? <p className="tools-empty">正在读取工具注册表…</p> : filtered.length === 0 ? <p className="tools-empty">{items.length ? '没有匹配的工具。' : '暂无已注册工具。工具创建与 MCP 连接管理需由服务端提供对应写入/健康检查 API。'}</p> : <div className="tools-table-wrap"><table className="tools-table"><thead><tr><th>工具</th><th>Server</th><th>版本</th><th>状态</th><th>Schema</th><th /></tr></thead><tbody>{filtered.map((item) => <tr key={item.item_id}><td><strong>{display(item.definition.name ?? item.item_id)}</strong><small>{item.item_id}</small></td><td>{display(item.definition.server_name ?? item.definition.server ?? item.definition.mcp_server)}</td><td>v{item.version}</td><td><span className="tools-status">{item.status}</span></td><td>{item.definition.input_schema && typeof item.definition.input_schema === 'object' ? '可用' : item.definition.json_schema && typeof item.definition.json_schema === 'object' ? '可用' : '未提供'}</td><td><Link className="text-link" to={'/admin/tools/' + encodeURIComponent(item.item_id)}>查看详情 →</Link></td></tr>)}</tbody></table></div>}
    </section>
    <p className="tools-note">此页面为只读注册表视图。当前已核实的后端契约没有 MCP Server 创建、连接测试或健康探测端点，因此不提供伪造的注册/连接按钮或在线状态。</p>
  </section>
}

function ToolDetail({ toolId }: { toolId: string }) {
  const [item, setItem] = useState<RegistryItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await apiClient.get<RegistryItem>(basePath + '/' + encodeURIComponent(toolId) + '/active')
      setItem(result)
    } catch (reason) {
      setError(errorMessage(reason))
      setItem(null)
    } finally {
      setLoading(false)
    }
  }, [toolId])
  useEffect(() => { void load() }, [load])

  const schema = item?.definition.input_schema ?? item?.definition.json_schema ?? item?.definition.parameters_schema
  const server = item?.definition.server_name ?? item?.definition.server ?? item?.definition.mcp_server
  return <section className="page-content tools-page">
    <Link className="text-link" to="/admin/tools">← 返回工具目录</Link>
    <header className="tools-heading"><div><p className="eyebrow">TOOL DETAIL · {toolId}</p><h1>{display(item?.definition.name ?? toolId)}</h1><p>{display(item?.definition.description)}</p></div><button className="button-primary" type="button" onClick={() => void load()} disabled={loading}>刷新详情</button></header>
    {loading ? <p className="tools-empty">正在读取 active 工具版本…</p> : error ? <div className="tools-alert" role="alert">{error}</div> : item ? <>
      <div className="tools-stats"><article><span>工具 ID</span><strong className="tools-stat-text">{item.item_id}</strong></article><article><span>版本</span><strong>v{item.version}</strong></article><article><span>状态</span><strong className="tools-stat-text">{item.status}</strong></article></div>
      <section className="tools-panel"><h2>注册元数据</h2><dl className="tools-definition"><dt>类型</dt><dd>{item.kind}</dd><dt>MCP Server</dt><dd>{display(server)}</dd><dt>创建者</dt><dd>{display(item.created_by)}</dd><dt>创建时间</dt><dd>{item.created_at ? new Date(item.created_at).toLocaleString() : '—'}</dd><dt>发布时间</dt><dd>{item.published_at ? new Date(item.published_at).toLocaleString() : '—'}</dd></dl></section>
      <section className="tools-panel"><h2>输入 JSON Schema</h2>{schema && typeof schema === 'object' ? <pre className="tools-json">{JSON.stringify(schema, null, 2)}</pre> : <p className="tools-empty">此工具定义未提供 input_schema / json_schema。</p>}</section>
      <section className="tools-panel"><h2>完整定义</h2><pre className="tools-json">{JSON.stringify(item.definition, null, 2)}</pre></section>
    </> : <p className="tools-empty">未找到 active 版本。</p>}
  </section>
}
