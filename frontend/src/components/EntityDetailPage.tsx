import { useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { ErrorState, LoadingState, NotFoundState } from '@/components/States'
import { MarkdownView } from '@/components/MarkdownView'
import { ApiError, apiClient } from '@/lib/api'
import {
  displayValue,
  fieldLabel,
  formatDateTime,
  localizedJson,
  valueLabel,
} from '@/lib/i18n/labels'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function DetailFields({ data }: { data: Record<string, unknown> }) {
  const outputText =
    typeof data.output_text === 'string' && data.output_text.trim()
      ? data.output_text
      : null
  const hasLastError =
    typeof data.last_error === 'string' && data.last_error.trim()
  return (
    <dl className="detail-fields">
      {Object.entries(data).map(([key, value]) => {
        // output_text 是派生字段，不单独展示，只作为 output 的渲染内容
        if (key === 'output_text') return null
        // last_error 在有 output_text 时折叠在 output 块下面，弱化展示
        if (key === 'last_error' && outputText && hasLastError) {
          return null
        }
        // output 字段如果有纯文本就优先展示纯文本，否则按 compound 处理
        if (key === 'output' && outputText) {
          return (
            <div key={key} className="detail-field detail-field--output">
              <dt>{fieldLabel(key)}</dt>
              <dd>
                <MarkdownView text={outputText} />
                {hasLastError && (
                  <p className="detail-run-error" role="alert">
                    本次运行曾失败：{data.last_error as string}
                  </p>
                )}
              </dd>
            </div>
          )
        }
        const compound = isRecord(value) || Array.isArray(value)
        return (
          <div key={key} className="detail-field">
            <dt>{fieldLabel(key)}</dt>
            <dd>
              {compound ? (
                <pre className="detail-json">{localizedJson(value)}</pre>
              ) : (
                displayValue(key, value)
              )}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

function DetailBody({ data }: { data: unknown }) {
  if (Array.isArray(data)) {
    if (data.length === 0) {
      return <p className="detail-empty">没有记录。</p>
    }
    return <pre className="detail-json">{localizedJson(data)}</pre>
  }
  if (isRecord(data)) {
    const items = data.items
    if (Array.isArray(items)) {
      return (
        <>
          <DetailFields
            data={Object.fromEntries(
              Object.entries(data).filter(([key]) => key !== 'items'),
            )}
          />
          <h2 className="detail-section-title">明细（{items.length}）</h2>
          {items.length === 0 ? (
            <p className="detail-empty">没有记录。</p>
          ) : (
            <div className="generic-table-wrap">
              <table className="generic-table">
                <tbody>
                  {items.map((item, index) => (
                    <tr key={index}>
                      <td>
                        <pre className="detail-json">
                          {localizedJson(item)}
                        </pre>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )
    }
    return <DetailFields data={data} />
  }
  return <pre className="detail-json">{localizedJson(data)}</pre>
}

export function EntityDetailPage({
  title,
  description,
  apiPath,
  actions,
}: {
  title: string
  description?: string
  apiPath: string
  actions?: ReactNode
}) {
  const query = useQuery({
    queryKey: ['entity-detail', apiPath],
    queryFn: () => apiClient.get<unknown>(apiPath),
    retry: false,
  })

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">详情</p>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
        <small>数据来自服务端接口</small>
      </div>
      {actions}
      {query.isPending && <LoadingState />}
      {query.isError && (
        <ErrorState
          description={
            query.error instanceof Error ? query.error.message : '详情加载失败'
          }
          onRetry={() => void query.refetch()}
        />
      )}
      {query.data !== undefined && <DetailBody data={query.data} />}
      <p className="detail-back">
        <Link className="text-link" to="/workspace/overview">
          返回运营驾驶舱
        </Link>
      </p>
    </section>
  )
}

const REGISTRY_DETAILS: Record<
  string,
  { title: string; label: string; kind: string; param: string }
> = {
  '/admin/models/providers/:providerId': {
    title: '模型供应商详情',
    label: '模型供应商',
    kind: 'model_provider',
    param: 'providerId',
  },
  '/admin/models/templates/:templateId': {
    title: '模型模板详情',
    label: '模型模板',
    kind: 'model_template',
    param: 'templateId',
  },
  '/admin/models/routing/:policyId': {
    title: '模型路由策略详情',
    label: '路由策略',
    kind: 'routing_policy',
    param: 'policyId',
  },
  '/admin/tools/:toolId': {
    title: '工具详情',
    label: '工具',
    kind: 'tool',
    param: 'toolId',
  },
  '/admin/skills/:skillId': {
    title: '技能包详情',
    label: '技能包',
    kind: 'skill',
    param: 'skillId',
  },
  '/operations/agents/:agentId': {
    title: '智能体运行配置',
    label: '智能体',
    kind: 'agent',
    param: 'agentId',
  },
}

interface RegistryItem {
  kind: string
  item_id: string
  version: number
  definition: Record<string, unknown>
  status: string
  created_by?: string
  created_at: string
  published_at: string | null
}

interface RegistryDiffChange {
  path: string
  operation: 'add' | 'remove' | 'replace'
  before?: unknown
  after?: unknown
}

interface RegistryDiff {
  kind: string
  item_id: string
  from_version: number
  to_version: number
  from_status: string
  to_status: string
  changes: RegistryDiffChange[]
}

const DIFF_OPERATIONS: Record<string, string> = {
  add: '新增',
  remove: '删除',
  replace: '替换',
}

const registryErrorText = (error: unknown): string => {
  if (error instanceof ApiError) {
    const detail = error.message ? `服务端信息：${error.message}` : ''
    switch (error.status) {
      case 401:
        return '登录状态已失效（401），请重新登录后再试。'
      case 403:
        return '当前账号缺少该操作所需的权限（403），请联系租户管理员。'
      case 404:
        return `当前部署未启用该能力，或目标版本不存在（404）。${detail}`
      case 409:
        return `当前状态与请求冲突（409），请刷新版本状态后重试。${detail}`
      case 422:
        return `定义未通过服务端校验（422）。${detail}`
      case 503:
        return `服务端尚未配置注册表能力（503）。${detail}`
      default:
        return detail || '操作失败，请稍后重试。'
    }
  }
  return error instanceof Error ? error.message : '操作失败，请稍后重试。'
}

const diffText = (value: unknown): string => {
  if (value === undefined) return '—'
  if (typeof value === 'string') return value
  return localizedJson(value)
}

function RegistryItemDetailPage({
  kind,
  itemId,
  title,
  label,
}: {
  kind: string
  itemId: string
  title: string
  label: string
}) {
  const queryClient = useQueryClient()
  const [fromVersion, setFromVersion] = useState<number | null>(null)
  const [toVersion, setToVersion] = useState<number | null>(null)

  const active = useQuery({
    queryKey: ['registry-detail', kind, itemId, 'active'],
    queryFn: () =>
      apiClient.get<RegistryItem>(
        `/platform/v1/registry/${kind}/${encodeURIComponent(itemId)}/active`,
      ),
    retry: false,
  })
  const versions = useQuery({
    queryKey: ['registry-detail', kind, itemId, 'versions'],
    queryFn: () =>
      apiClient.get<{ items: RegistryItem[] }>(
        `/platform/v1/registry/${kind}/${encodeURIComponent(itemId)}/versions`,
      ),
    retry: false,
  })

  const sortedVersions = useMemo(
    () =>
      [...(versions.data?.items ?? [])].sort(
        (left, right) => right.version - left.version,
      ),
    [versions.data],
  )
  const from = fromVersion ?? sortedVersions[1]?.version ?? null
  const to = toVersion ?? sortedVersions[0]?.version ?? null
  const comparable = from !== null && to !== null && from !== to

  const diff = useQuery({
    queryKey: ['registry-detail', kind, itemId, 'diff', from, to],
    queryFn: () =>
      apiClient.get<RegistryDiff>(
        `/platform/v1/registry/${kind}/${encodeURIComponent(itemId)}/diff/${from}/${to}`,
      ),
    enabled: comparable,
    retry: false,
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['registry', kind] })
    void queryClient.invalidateQueries({
      queryKey: ['registry-detail', kind, itemId],
    })
  }

  const publish = useMutation({
    mutationFn: (version: number) =>
      apiClient.post(
        `/platform/v1/registry/${kind}/${encodeURIComponent(itemId)}/versions/${version}/publish`,
      ),
    onSuccess: invalidate,
  })
  const rollback = useMutation({
    mutationFn: (version: number) =>
      apiClient.post(
        `/platform/v1/registry/${kind}/${encodeURIComponent(itemId)}/rollback/${version}`,
      ),
    onSuccess: invalidate,
  })
  const disable = useMutation({
    mutationFn: () =>
      apiClient.post(
        `/platform/v1/registry/${kind}/${encodeURIComponent(itemId)}/disable`,
      ),
    onSuccess: invalidate,
  })

  const mutationError = [publish, rollback, disable].find(
    (item) => item.isError,
  )?.error
  const busy = publish.isPending || rollback.isPending || disable.isPending

  const rollbackTo = (version: number) => {
    if (
      !window.confirm(
        `确认将 ${itemId} 回滚到 v${version}？回滚后运行时将解析该版本。`,
      )
    ) {
      return
    }
    rollback.mutate(version)
  }

  const disableItem = () => {
    if (
      !window.confirm(
        `确认停用 ${itemId}？停用后运行时不会再解析到该条目。`,
      )
    ) {
      return
    }
    disable.mutate()
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">详情 · 注册表</p>
        <h1>{title}</h1>
        <p>
          {label}：{itemId}
        </p>
        <small>
          保存会生成新版本，发布或回滚后运行时才会解析新版本。
        </small>
      </div>

      <div className="detail-actions">
        <button
          className="button-secondary"
          type="button"
          disabled={active.isFetching || versions.isFetching}
          onClick={() => {
            void active.refetch()
            void versions.refetch()
          }}
        >
          刷新
        </button>
        {active.data && active.data.status !== 'disabled' && (
          <button
            className="button-secondary"
            type="button"
            disabled={busy}
            onClick={disableItem}
          >
            停用
          </button>
        )}
        {mutationError && (
          <span className="auth-error" role="alert">
            {registryErrorText(mutationError)}
          </span>
        )}
      </div>

      {active.isPending && <LoadingState label="正在读取生效版本" />}
      {active.isError && (
        <ErrorState
          title="生效版本不可用"
          description={registryErrorText(active.error)}
          onRetry={() => void active.refetch()}
        />
      )}
      {active.data && (
        <DetailFields data={active.data as unknown as Record<string, unknown>} />
      )}

      <h2 className="detail-section-title">
        版本历史（{sortedVersions.length}）
      </h2>
      {versions.isPending && <LoadingState label="正在读取版本历史" />}
      {versions.isError && (
        <ErrorState
          title="版本历史加载失败"
          description={registryErrorText(versions.error)}
          onRetry={() => void versions.refetch()}
        />
      )}
      {versions.data && sortedVersions.length === 0 && (
        <p className="detail-empty">当前条目还没有任何版本。</p>
      )}
      {sortedVersions.length > 0 && (
        <div className="generic-table-wrap">
          <table className="generic-table">
            <thead>
              <tr>
                <th>版本</th>
                <th>状态</th>
                <th>创建人</th>
                <th>创建时间</th>
                <th>发布时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {sortedVersions.map((item) => (
                <tr key={item.version}>
                  <td>
                    v{item.version}
                    {active.data?.version === item.version && (
                      <span className="registry-status registry-status--published">
                        当前生效
                      </span>
                    )}
                  </td>
                  <td>
                    <span
                      className={`registry-status registry-status--${item.status}`}
                    >
                      {valueLabel(item.status)}
                    </span>
                  </td>
                  <td>{item.created_by || '—'}</td>
                  <td className="cell-nowrap">
                    {formatDateTime(item.created_at)}
                  </td>
                  <td className="cell-nowrap">
                    {formatDateTime(item.published_at)}
                  </td>
                  <td className="cell-nowrap crud-actions">
                    {item.status !== 'published' && (
                      <button
                        className="text-link"
                        type="button"
                        disabled={busy}
                        onClick={() => publish.mutate(item.version)}
                      >
                        发布
                      </button>
                    )}
                    {active.data && item.version !== active.data.version && (
                      <button
                        className="text-link"
                        type="button"
                        disabled={busy}
                        onClick={() => rollbackTo(item.version)}
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

      <h2 className="detail-section-title">版本对比</h2>
      {sortedVersions.length < 2 ? (
        <p className="detail-empty">
          至少需要两个版本才能对比；保存一次新版本后即可查看差异。
        </p>
      ) : (
        <>
          <div className="crud-toolbar">
            <label className="editor-field">
              <span>起始版本</span>
              <select
                value={from ?? ''}
                onChange={(event) => setFromVersion(Number(event.target.value))}
              >
                {sortedVersions.map((item) => (
                  <option value={item.version} key={`from-${item.version}`}>
                    v{item.version}（{valueLabel(item.status)}）
                  </option>
                ))}
              </select>
            </label>
            <label className="editor-field">
              <span>对比版本</span>
              <select
                value={to ?? ''}
                onChange={(event) => setToVersion(Number(event.target.value))}
              >
                {sortedVersions.map((item) => (
                  <option value={item.version} key={`to-${item.version}`}>
                    v{item.version}（{valueLabel(item.status)}）
                  </option>
                ))}
              </select>
            </label>
          </div>
          {!comparable && (
            <p className="crud-error" role="alert">
              请选择两个不同版本进行对比。
            </p>
          )}
          {comparable && diff.isPending && (
            <LoadingState label="正在读取版本差异" />
          )}
          {comparable && diff.isError && (
            <ErrorState
              title="版本对比不可用"
              description={registryErrorText(diff.error)}
              onRetry={() => void diff.refetch()}
            />
          )}
          {comparable && diff.data && (
            <>
              <p className="detail-empty">
                v{diff.data.from_version}（{valueLabel(diff.data.from_status)}）→ v
                {diff.data.to_version}（{valueLabel(diff.data.to_status)}），共{' '}
                {diff.data.changes.length} 处变更。
              </p>
              {diff.data.changes.length === 0 ? (
                <p className="detail-empty">两个版本的定义完全一致。</p>
              ) : (
                <div className="generic-table-wrap">
                  <table className="generic-table">
                    <thead>
                      <tr>
                        <th>路径</th>
                        <th>操作</th>
                        <th>变更前</th>
                        <th>变更后</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diff.data.changes.map((change, index) => (
                        <tr key={`${change.path}-${change.operation}-${index}`}>
                          <td className="cell-nowrap">{change.path}</td>
                          <td>{DIFF_OPERATIONS[change.operation] ?? change.operation}</td>
                          <td>{diffText(change.before)}</td>
                          <td>{diffText(change.after)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </>
      )}

      <p className="detail-back">
        <Link className="text-link" to="/workspace/overview">
          返回运营驾驶舱
        </Link>
      </p>
    </section>
  )
}

/** Resolves every remaining detail route to a real backend endpoint. */
export function DetailRoutePage({ path }: { path: string }) {
  const params = useParams()

  if (path === '/operations/memories/:memoryId') {
    return (
      <EntityDetailPage
        title="记忆详情"
        description="记忆版本历史（审计口径）"
        apiPath={`/platform/v1/memories/${params.memoryId}/history`}
      />
    )
  }
  if (path === '/operations/conversations/:conversationId') {
    return (
      <EntityDetailPage
        title="会话详情"
        apiPath={`/platform/v1/sessions/${params.conversationId}`}
      />
    )
  }
  if (path === '/operations/files/:fileId') {
    return <FileDetailPage fileId={params.fileId!} />
  }
  if (path === '/operations/workflow-runs/:runId') {
    return (
      <EntityDetailPage
        title="工作流运行详情"
        apiPath={`/platform/v1/runs/${params.runId}`}
      />
    )
  }
  if (path === '/operations/evaluation/datasets/:datasetId') {
    return (
      <EntityDetailPage
        title="评测数据集版本"
        apiPath={`/platform/v1/observability/evaluations/datasets/${params.datasetId}/versions`}
      />
    )
  }
  if (path === '/operations/evaluation/runs/:evaluationRunId') {
    return (
      <EntityDetailPage
        title="评测运行评分"
        apiPath={`/platform/v1/observability/evaluations/${params.evaluationRunId}/scores`}
      />
    )
  }
  const registry = REGISTRY_DETAILS[path]
  if (registry) {
    return (
      <RegistryItemDetailPage
        title={registry.title}
        label={registry.label}
        kind={registry.kind}
        itemId={params[registry.param] ?? ''}
      />
    )
  }
  return <NotFoundState />
}

// --------------------------------------------------------------------------- File Preview

/** Simple file previewer: fetch binary blob + render by media type. */
function FilePreview({ assetId, mediaType }: { assetId: string; mediaType?: string }) {
  const previewQuery = useQuery({
    queryKey: ['file-preview', assetId],
    queryFn: async () => {
      const res = await fetch(`/api/platform/v1/files/${encodeURIComponent(assetId)}/content`, { credentials: 'same-origin' })
      if (!res.ok) throw new Error(`下载失败 ${res.status}`)
      return res.blob()
    },
    retry: false,
  })

  if (previewQuery.isPending) return <LoadingState label="正在加载文件内容" />
  if (previewQuery.isError)
    return (
      <ErrorState
        title="文件内容加载失败"
        description={previewQuery.error instanceof Error ? previewQuery.error.message : '无法预览'}
      />
    )

  const blob = previewQuery.data!
  const type = mediaType || blob.type || 'application/octet-stream'
  const url = URL.createObjectURL(blob)
  const kb = Math.round(blob.size / 1024)

  const isText = type.startsWith('text/') || type === 'application/json' || type.endsWith('+json') || type.includes('xml')
  const isImage = type.startsWith('image/')
  const isPdf = type === 'application/pdf'
  const isCsv = type === 'text/csv'

  if (isText || isCsv) {
    // 文本：截断到 200KB 纯文本，防止巨大文件卡 UI
    return (
      <div className="file-preview">
        <p className="file-preview-meta">
          {type} · {kb} KB · 共 {blob.size.toLocaleString()} 字节
        </p>
        <TextBlobView blob={blob} maxBytes={200 * 1024} />
      </div>
    )
  }
  if (isImage) {
    return (
      <div className="file-preview">
        <p className="file-preview-meta">
          {type} · {kb} KB
        </p>
        <img className="file-preview-img" src={url} alt={`文件 ${assetId}`} />
      </div>
    )
  }
  if (isPdf) {
    return (
      <div className="file-preview">
        <p className="file-preview-meta">
          {type} · {kb} KB
        </p>
        <iframe className="file-preview-iframe" src={url} title="PDF 预览" />
      </div>
    )
  }
  return (
    <div className="file-preview file-preview-unsupported">
      <p>
        当前文件类型 <code>{type}</code> 暂不支持在线预览。
      </p>
      <p>文件大小：{kb} KB · {blob.size.toLocaleString()} 字节</p>
      <a className="btn btn-primary" href={`/api/platform/v1/files/${encodeURIComponent(assetId)}/content`} download>
        下载文件
      </a>
    </div>
  )
}

function TextBlobView({ blob, maxBytes }: { blob: Blob; maxBytes: number }) {
  const [text, setText] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  useMemo(() => {
    // 截断：只取前 maxBytes
    const slice = blob.slice(0, maxBytes)
    slice.text().then((t) => {
      setText(t)
      setTruncated(blob.size > maxBytes)
    })
  }, [blob, maxBytes])
  if (text === null) return <LoadingState label="解析文本中" />
  return (
    <>
      <pre className="file-preview-text">{text}</pre>
      {truncated && <p className="file-preview-truncated">仅预览前 {(maxBytes / 1024).toFixed(0)} KB，完整内容请下载。</p>}
    </>
  )
}

/** File detail page with metadata + inline preview. */
export function FileDetailPage({ fileId }: { fileId: string }) {
  const metaQuery = useQuery({
    queryKey: ['entity-detail', `/platform/v1/files/${fileId}/metadata`],
    queryFn: () => apiClient.get<Record<string, unknown>>(`/platform/v1/files/${fileId}/metadata`),
    retry: false,
  })

  // 固定展示顺序，过滤掉 asset_id（文件 ID 不展示）
  const FIELD_ORDER = [
    'filename', 'status', 'media_type', 'size_bytes',
    'agent_id', 'owner_id',
    'session_label', 'project_id',
    'sha256', 'version', 'created_at',
  ]

  const data = metaQuery.data ?? {}

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">文件详情</p>
        <h1>{typeof data.filename === 'string' ? data.filename : '文件详情'}</h1>
        {metaQuery.data && (
          <p className="muted">
            {String(metaQuery.data.media_type ?? '')} · {String(metaQuery.data.size_bytes ?? 0)} 字节
          </p>
        )}
      </div>
      {metaQuery.isPending && <LoadingState />}
      {metaQuery.isError && (
        <ErrorState description={metaQuery.error instanceof Error ? metaQuery.error.message : '详情加载失败'} onRetry={() => void metaQuery.refetch()} />
      )}
      {metaQuery.data && (
        <>
          <div className="detail-block">
            <h2>基本信息</h2>
            <dl className="detail-fields">
              {FIELD_ORDER.map((key) => {
                // session_label 优先展示，如果没有就 fallback 到 session_id 的缩写
                if (key === 'session_label') {
                  const label = data.session_label as string | undefined
                  if (label) {
                    return (
                      <div key="session_label" className="detail-field">
                        <dt>{fieldLabel('session_label')}</dt>
                        <dd>{label}</dd>
                      </div>
                    )
                  }
                  return null
                }
                if (!(key in data)) return null
                const value = data[key]
                if (value === null || value === undefined || value === '') return null
                const compound = isRecord(value) || Array.isArray(value)
                return (
                  <div key={key} className="detail-field">
                    <dt>{fieldLabel(key)}</dt>
                    <dd>
                      {compound ? (
                        <pre className="detail-json">{localizedJson(value)}</pre>
                      ) : (
                        displayValue(key, value)
                      )}
                    </dd>
                  </div>
                )
              })}
            </dl>
          </div>
          <div className="detail-block">
            <h2>文件预览</h2>
            <FilePreview assetId={fileId} mediaType={typeof data.media_type === 'string' ? data.media_type : undefined} />
          </div>
        </>
      )}
    </section>
  )
}
