import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import { valueLabel } from '@/lib/i18n/labels'

interface RegistryItem {
  kind: string
  item_id: string
  version: number
  definition: Record<string, unknown>
  status: string
  created_at: string
  published_at: string | null
  used_by?: string[]
}

type ResourceKind = 'tool' | 'skill'

interface ConnectionTestResult {
  status: string
  checked_at: string
  latency_ms: number
  expected_tools: string[]
  discovered_tools: string[]
  error_code: string | null
}

interface SkillEvaluationResult {
  status: string
  skill_version: number
  manifest_digest: string
  dependencies: Record<string, string>
  findings: string[]
  reviewed_at: string
}

interface WorkbenchCapabilities {
  features: Record<string, boolean>
}

function safeValue(value: unknown, key = ''): string {
  if (/(secret|token|api.?key|password|credential)/i.test(key)) {
    return '[敏感字段已隐藏；请使用 secret_ref]'
  }
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function Field({ label, value, field }: { label: string; value: unknown; field?: string }) {
  return (
    <div className="detail-field">
      <dt>{label}</dt>
      <dd><pre>{safeValue(value, field)}</pre></dd>
    </div>
  )
}

export function ToolSkillResourcePage({ kind }: { kind: ResourceKind }) {
  const params = useParams()
  const itemId = kind === 'tool' ? params.toolId : params.skillId
  const title = kind === 'tool' ? '工具与 MCP' : 'Skill 技能包'
  const base = ['registry', kind]
  const queryClient = useQueryClient()

  const list = useQuery({
    queryKey: base,
    queryFn: () => apiClient.get<{ items: RegistryItem[] }>(`/platform/v1/registry/${kind}`),
    retry: false,
  })
  const capabilities = useQuery({
    queryKey: ['workbench', 'capabilities'],
    queryFn: () =>
      apiClient.get<WorkbenchCapabilities>('/platform/v1/workbench/capabilities'),
    retry: false,
  })
  const active = useQuery({
    queryKey: [...base, itemId, 'active'],
    queryFn: () => apiClient.get<RegistryItem>(`/platform/v1/registry/${kind}/${encodeURIComponent(itemId!)}/active`),
    enabled: Boolean(itemId),
    retry: false,
  })
  const versions = useQuery({
    queryKey: [...base, itemId, 'versions'],
    queryFn: () => apiClient.get<{ items: RegistryItem[] }>(`/platform/v1/registry/${kind}/${encodeURIComponent(itemId!)}/versions`),
    enabled: Boolean(itemId),
    retry: false,
  })
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: base })
    void queryClient.invalidateQueries({ queryKey: [...base, itemId] })
  }
  const publish = useMutation({
    mutationFn: (version: number) => apiClient.post(`/platform/v1/registry/${kind}/${encodeURIComponent(itemId!)}/versions/${version}/publish`),
    onSuccess: invalidate,
  })
  const rollback = useMutation({
    mutationFn: (version: number) => apiClient.post(`/platform/v1/registry/${kind}/${encodeURIComponent(itemId!)}/rollback/${version}`),
    onSuccess: invalidate,
  })
  const disable = useMutation({
    mutationFn: () => apiClient.post(`/platform/v1/registry/${kind}/${encodeURIComponent(itemId!)}/disable`),
    onSuccess: invalidate,
  })
  const connectionTest = useMutation({
    mutationFn: () =>
      apiClient.post<ConnectionTestResult>(
        `/v1/tools/${encodeURIComponent(itemId!)}/test-connection`,
        { version: active.data!.version },
      ),
  })
  const compatibilityEvaluation = useMutation({
    mutationFn: () =>
      apiClient.post<SkillEvaluationResult>(
        `/v1/skills/${encodeURIComponent(itemId!)}/evaluate`,
        { version: active.data!.version },
      ),
  })

  if (!itemId) {
    return (
      <section className="page-content">
        <div className="page-heading">
          <h1>{title}</h1>
          <p>资源列表由租户 Registry 提供；选择资源查看生效定义、依赖引用与版本生命周期。</p>
        </div>
        {list.isPending && <LoadingState />}
        {list.isError && <ErrorState title={`${title}加载失败`} description={list.error instanceof Error ? list.error.message : '请求失败'} onRetry={() => void list.refetch()} />}
        {list.data && (
          <div className="generic-table-wrap">
            <table className="generic-table">
              <thead><tr><th>ID</th><th>名称</th>{kind === 'tool' ? <><th>风险</th><th>传输方式</th><th>被 Agent 使用</th></> : <><th>模式</th><th>工具依赖</th><th>被 Agent 使用</th></>}<th>版本</th><th>状态</th><th>详情</th></tr></thead>
              <tbody>
                {list.data.items.map((item) => (
                  <tr key={item.item_id}>
                    <td>{item.item_id}</td>
                    <td>{String(item.definition.name ?? '—')}</td>
                    {kind === 'tool' ? <>
                      <td>{String(item.definition.risk_level ?? '—')}</td>
                      <td>{String(item.definition.transport ?? item.definition.protocol ?? '—')}</td>
                    </> : <>
                      <td>{String(item.definition.mode ?? '—')}</td>
                      <td>{Array.isArray(item.definition.tool_ids) ? item.definition.tool_ids.join(', ') : '—'}</td>
                    </>}
                    <td>{item.used_by?.length ? item.used_by.join(', ') : '—'}</td>
                    <td>v{item.version}</td>
                    <td>{valueLabel(item.status)}</td>
                    <td><Link className="text-link" to={`/admin/${kind === 'tool' ? 'tools' : 'skills'}/${encodeURIComponent(item.item_id)}`}>查看</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    )
  }

  const definition = active.data?.definition ?? {}
  const error = [
    publish,
    rollback,
    disable,
    connectionTest,
    compatibilityEvaluation,
  ].find((mutation) => mutation.isError)?.error
  const fields = kind === 'tool'
    ? [
        ['描述', definition.description, 'description'],
        ['传输方式', definition.transport ?? definition.protocol, 'transport'],
        ['端点', definition.endpoint ?? definition.url, 'endpoint'],
        ['认证引用', definition.secret_ref ?? definition.auth_secret_ref, 'secret_ref'],
        ['风险等级', definition.risk_level, 'risk_level'],
        ['授权策略', definition.authorizer ?? definition.authorization, 'authorizer'],
        ['Allowlist', definition.allowlist, 'allowlist'],
        ['超时', definition.timeout_ms, 'timeout_ms'],
        ['重试策略', definition.retry, 'retry'],
        ['输入 JSON Schema', definition.input_schema, 'input_schema'],
        ['输出 JSON Schema', definition.output_schema, 'output_schema'],
      ] as const
    : [
        ['描述', definition.description, 'description'],
        ['内容格式', definition.content_format ?? definition.format, 'content_format'],
        ['来源', definition.source, 'source'],
        ['文件引用', definition.file_refs ?? definition.files, 'file_refs'],
        ['Manifest', definition.manifest, 'manifest'],
        ['工具依赖', definition.tool_ids ?? definition.dependencies, 'tool_ids'],
        ['适用 Agent', list.data?.items.find((item) => item.item_id === itemId)?.used_by, 'used_by'],
        ['兼容性声明', definition.compatibility, 'compatibility'],
      ] as const

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow"><Link to={`/admin/${kind === 'tool' ? 'tools' : 'skills'}`}>{title}</Link> · 资源详情</p>
        <h1>{String(definition.name ?? itemId)}</h1>
        <p>资源 ID：{itemId}</p>
        {active.data && <small>生效版本 v{active.data.version} · {valueLabel(active.data.status)}</small>}
      </div>
      {active.isPending && <LoadingState />}
      {active.isError && <ErrorState title="无法读取生效版本" description={active.error instanceof Error ? active.error.message : '该资源尚无生效版本或无读取权限。'} />}
      {active.data && <>
        <div className="detail-actions">
          <button className="button-secondary" type="button" disabled={disable.isPending || active.data.status === 'disabled'} onClick={() => disable.mutate()}>停用资源</button>
          {kind === 'tool' && capabilities.isPending && (
            <span className="detail-empty">正在检查连接测试能力…</span>
          )}
          {kind === 'tool' && capabilities.data?.features.tool_connection_test === true && (
            <button
              className="button-secondary"
              type="button"
              disabled={connectionTest.isPending || active.data.status !== 'published'}
              onClick={() => connectionTest.mutate()}
            >
              {connectionTest.isPending ? '连接测试中…' : '测试连接'}
            </button>
          )}
          {kind === 'tool' &&
            !capabilities.isPending &&
            capabilities.data?.features.tool_connection_test !== true && (
              <span className="detail-empty">连接测试能力未配置或不可用。</span>
            )}
          {kind === 'skill' && capabilities.isPending && (
            <span className="detail-empty">正在检查兼容性评估能力…</span>
          )}
          {kind === 'skill' &&
            capabilities.data?.features.skill_compatibility_evaluation === true && (
              <button
                className="button-secondary"
                type="button"
                disabled={
                  compatibilityEvaluation.isPending || active.data.status !== 'published'
                }
                onClick={() => compatibilityEvaluation.mutate()}
              >
                {compatibilityEvaluation.isPending ? '评估中…' : '评估兼容性'}
              </button>
            )}
          {kind === 'skill' &&
            !capabilities.isPending &&
            capabilities.data?.features.skill_compatibility_evaluation !== true && (
              <span className="detail-empty">兼容性评估能力未配置或不可用。</span>
            )}
          {error && <span className="auth-error" role="alert">{error instanceof Error ? error.message : '操作失败'}</span>}
        </div>
        {kind === 'tool' && connectionTest.data && (
          <section className="detail-section">
            <h2 className="detail-section-title">最近连接测试</h2>
            <p>
              状态：{connectionTest.data.status} · 延迟：
              {connectionTest.data.latency_ms} ms
            </p>
            {connectionTest.data.error_code && (
              <p role="status">错误代码：{connectionTest.data.error_code}</p>
            )}
            <p>发现工具：{connectionTest.data.discovered_tools.join(', ') || '无'}</p>
          </section>
        )}
        {kind === 'skill' && compatibilityEvaluation.data && (
          <section className="detail-section">
            <h2 className="detail-section-title">兼容性评估</h2>
            <p>
              结果：{compatibilityEvaluation.data.status} · 版本：
              v{compatibilityEvaluation.data.skill_version}
            </p>
            <p>Manifest Digest：{compatibilityEvaluation.data.manifest_digest}</p>
            <p>发现项：{compatibilityEvaluation.data.findings.join('；') || '无'}</p>
          </section>
        )}
        <h2 className="detail-section-title">配置与治理信息</h2>

        <dl className="detail-fields">
          {fields.map(([label, value, field]) => <Field key={field} label={label} value={value} field={field} />)}
        </dl>
      </>}
      <h2 className="detail-section-title">版本历史（{versions.data?.items.length ?? 0}）</h2>
      {versions.isPending && <LoadingState />}
      {versions.isError && <ErrorState title="版本历史加载失败" description={versions.error instanceof Error ? versions.error.message : '请求失败'} onRetry={() => void versions.refetch()} />}
      {versions.data && <div className="generic-table-wrap"><table className="generic-table">
        <thead><tr><th>版本</th><th>状态</th><th>创建时间</th><th>发布时间</th><th>操作</th></tr></thead>
        <tbody>{[...versions.data.items].reverse().map((item) => <tr key={item.version}>
          <td>v{item.version}</td><td>{valueLabel(item.status)}</td><td>{new Date(item.created_at).toLocaleString('zh-CN')}</td><td>{item.published_at ? new Date(item.published_at).toLocaleString('zh-CN') : '—'}</td>
          <td className="cell-nowrap">
            {item.status !== 'published' && <button className="text-link" type="button" disabled={publish.isPending} onClick={() => publish.mutate(item.version)}>发布</button>}
            {active.data && item.version !== active.data.version && <button className="text-link" type="button" disabled={rollback.isPending} onClick={() => rollback.mutate(item.version)}>回滚</button>}
          </td>
        </tr>)}</tbody>
      </table></div>}
    </section>
  )
}
