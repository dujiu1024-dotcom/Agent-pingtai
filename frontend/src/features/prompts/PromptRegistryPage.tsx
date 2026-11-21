import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api'

interface PromptSummary {
  prompt_id: string
  latest_version: number
  active_version: number | null
  canary_version: number | null
  canary_percentage: number | null
  versions_count: number
  variables: string[]
  content_hash: string
}

interface PromptVersion {
  prompt_id: string
  version: number
  variables: string[]
  content_hash: string
  template?: string
}

interface ListResponse<T> {
  items: T[]
  count: number
}

const promptPath = (id: string) =>
  '/platform/v1/prompts/' + encodeURIComponent(id)

const shortHash = (value: string) => (value ? value.slice(0, 12) : '—')

export function PromptRegistryPage() {
  const { promptId } = useParams<{ promptId: string }>()
  const queryClient = useQueryClient()
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [baseVersion, setBaseVersion] = useState<number | null>(null)
  const [draftTemplate, setDraftTemplate] = useState('')
  const [draftError, setDraftError] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['prompt-registry'],
    queryFn: () =>
      apiClient.get<ListResponse<PromptSummary>>(
        '/platform/v1/prompts/?limit=200',
      ),
    enabled: !promptId,
  })

  const versionsQuery = useQuery({
    queryKey: ['prompt-versions', promptId],
    queryFn: () =>
      apiClient.get<ListResponse<PromptVersion>>(
        promptPath(promptId!) + '/versions',
      ),
    enabled: Boolean(promptId),
  })

  const versions = versionsQuery.data?.items ?? []
  const currentVersion =
    selectedVersion ??
    (versions.length ? versions[versions.length - 1].version : null)
  const comparisonVersion =
    baseVersion ??
    [...versions]
      .reverse()
      .find((item) => item.version !== currentVersion)?.version ??
    null

  const detailQuery = useQuery({
    queryKey: ['prompt-version', promptId, currentVersion],
    queryFn: () =>
      apiClient.get<PromptVersion>(
        promptPath(promptId!) + '/versions/' + currentVersion,
      ),
    enabled: Boolean(promptId && currentVersion),
  })

  const diffQuery = useQuery({
    queryKey: ['prompt-diff', promptId, comparisonVersion, currentVersion],
    queryFn: () =>
      apiClient.get<{ diff: string }>(
        promptPath(promptId!) +
          '/versions/' +
          currentVersion +
          '/diff?base_version=' +
          comparisonVersion,
      ),
    enabled: Boolean(
      promptId &&
        currentVersion &&
        comparisonVersion &&
        currentVersion !== comparisonVersion,
    ),
  })

  const gateVersion = selectedVersion ?? currentVersion
  const gateQuery = useQuery({
    queryKey: ['prompt-evaluation-gate', promptId, gateVersion],
    queryFn: () =>
      apiClient.get<{
        status: 'passed' | 'not_recorded'
        eval_run_id: string | null
        action: string | null
      }>(
        promptPath(promptId!) + '/evaluation-gate?version=' + gateVersion,
      ),
    enabled: Boolean(promptId && gateVersion),
    retry: false,
  })

  const createDraft = useMutation({
    mutationFn: () =>
      apiClient.post<PromptVersion>(
        promptPath(promptId!) + '/versions',
        { template: draftTemplate },
      ),
    onSuccess: async (created) => {
      setDraftTemplate('')
      setDraftError(null)
      setSelectedVersion(created.version)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['prompt-registry'] }),
        queryClient.invalidateQueries({
          queryKey: ['prompt-versions', promptId],
        }),
      ])
    },
    onError: (error) => {
      setDraftError(error instanceof Error ? error.message : String(error))
    },
  })

  if (!promptId) {
    if (listQuery.isPending) {
      return <LoadingState label="正在加载 Prompt Registry" />
    }

    if (listQuery.isError) {
      return (
        <section className="page-content">
          <ErrorState
            title="Prompt 列表加载失败"
            description={
              listQuery.error instanceof Error
                ? listQuery.error.message
                : String(listQuery.error)
            }
            onRetry={() => void listQuery.refetch()}
          />
        </section>
      )
    }

    const items = listQuery.data?.items ?? []

    return (
      <section className="page-content prompt-registry">
        <div className="page-heading prompt-heading">
          <div>
            <p className="eyebrow">ADM-PROMPTS · #807</p>
            <h1>Prompt 与模板管理</h1>
            <p>
              按租户隔离的 Prompt 版本目录。选择条目查看版本、差异和评测发布约束。
            </p>
          </div>
          <span className="prompt-count">
            {listQuery.data?.count ?? 0} 个 Prompt
          </span>
        </div>

        {items.length === 0 ? (
          <div className="prompt-empty">
            <strong>暂无 Prompt</strong>
            <p>当前租户尚未登记 Prompt 版本。</p>
          </div>
        ) : (
          <div className="prompt-grid">
            {items.map((item) => (
              <Link
                className="prompt-card"
                key={item.prompt_id}
                to={'/admin/prompts/' + encodeURIComponent(item.prompt_id)}
              >
                <div className="prompt-card-top">
                  <strong>{item.prompt_id}</strong>
                  <span
                    className={
                      item.active_version
                        ? 'prompt-status active'
                        : 'prompt-status'
                    }
                  >
                    {item.active_version
                      ? '已激活 v' + item.active_version
                      : '未激活'}
                  </span>
                </div>
                <div className="prompt-card-meta">
                  <span>最新版本 v{item.latest_version}</span>
                  <span>{item.versions_count} 个版本</span>
                </div>
                <div className="prompt-card-meta">
                  <span>
                    变量：{item.variables.length ? item.variables.join(', ') : '无'}
                  </span>
                  <span>Hash {shortHash(item.content_hash)}</span>
                </div>
                {item.canary_version && (
                  <p className="prompt-canary">
                    灰度版本 v{item.canary_version} · {item.canary_percentage}%
                  </p>
                )}
              </Link>
            ))}
          </div>
        )}
      </section>
    )
  }

  if (versionsQuery.isPending) {
    return <LoadingState label="正在加载 Prompt 版本" />
  }

  if (versionsQuery.isError) {
    return (
      <section className="page-content">
        <ErrorState
          title="Prompt 详情加载失败"
          description={
            versionsQuery.error instanceof Error
              ? versionsQuery.error.message
              : String(versionsQuery.error)
          }
          onRetry={() => void versionsQuery.refetch()}
        />
      </section>
    )
  }

  return (
    <section className="page-content prompt-registry">
      <div className="page-heading prompt-heading">
        <div>
          <p className="eyebrow">ADM-PROMPTS · #807</p>
          <h1>{promptId}</h1>
          <p>版本历史、内容差异与草稿登记。</p>
        </div>
        <Link className="prompt-back-link" to="/admin/prompts">
          返回 Prompt 列表
        </Link>
      </div>

      <div className="prompt-detail-grid">
        <aside className="prompt-panel">
          <div className="prompt-panel-heading">
            <strong>版本历史</strong>
            <span>{versions.length} 个版本</span>
          </div>
          {versions.length === 0 ? (
            <p className="prompt-muted">暂无版本记录</p>
          ) : (
            <div className="prompt-version-list">
              {versions.map((item) => (
                <button
                  className={
                    currentVersion === item.version
                      ? 'prompt-version selected'
                      : 'prompt-version'
                  }
                  key={item.version}
                  onClick={() => setSelectedVersion(item.version)}
                  type="button"
                >
                  <strong>版本 v{item.version}</strong>
                  <small>
                    {item.variables.length
                      ? item.variables.join(', ')
                      : '无模板变量'}
                  </small>
                </button>
              ))}
            </div>
          )}
        </aside>

        <div className="prompt-main-column">
          <section className="prompt-panel">
            <div className="prompt-panel-heading">
              <strong>版本详情</strong>
              <span>
                {detailQuery.data
                  ? 'SHA ' + shortHash(detailQuery.data.content_hash)
                  : ''}
              </span>
            </div>
            {detailQuery.isPending && (
              <LoadingState label="正在读取版本内容" />
            )}
            {detailQuery.isError && (
              <ErrorState
                title="版本内容读取失败"
                description={
                  detailQuery.error instanceof Error
                    ? detailQuery.error.message
                    : String(detailQuery.error)
                }
                onRetry={() => void detailQuery.refetch()}
              />
            )}
            {detailQuery.data && (
              <>
                <div className="prompt-variable-list">
                  {detailQuery.data.variables.map((name) => (
                    <code key={name}>{name}</code>
                  ))}
                  {detailQuery.data.variables.length === 0 && (
                    <span className="prompt-muted">无模板变量</span>
                  )}
                </div>
                <pre className="prompt-template">
                  {detailQuery.data.template ?? '服务端未返回模板正文'}
                </pre>
              </>
            )}
          </section>

          <section className="prompt-panel">
            <div className="prompt-panel-heading">
              <strong>版本差异</strong>
              <span>选择两个版本进行对比</span>
            </div>
            <div className="prompt-diff-controls">
              <label>
                基准版本
                <select
                  value={comparisonVersion ?? ''}
                  onChange={(event) =>
                    setBaseVersion(
                      event.target.value ? Number(event.target.value) : null,
                    )
                  }
                >
                  <option value="">选择版本</option>
                  {versions.map((item) => (
                    <option key={item.version} value={item.version}>
                      v{item.version}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                对比版本
                <select
                  value={currentVersion ?? ''}
                  onChange={(event) =>
                    setSelectedVersion(
                      event.target.value ? Number(event.target.value) : null,
                    )
                  }
                >
                  {versions.map((item) => (
                    <option key={item.version} value={item.version}>
                      v{item.version}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {diffQuery.isPending && comparisonVersion && (
              <LoadingState label="正在计算版本差异" />
            )}
            {diffQuery.isError && (
              <p className="prompt-error">
                {diffQuery.error instanceof Error
                  ? diffQuery.error.message
                  : String(diffQuery.error)}
              </p>
            )}
            {diffQuery.data && (
              <pre className="prompt-diff">
                {diffQuery.data.diff || '两个版本内容一致'}
              </pre>
            )}
            {!comparisonVersion && (
              <p className="prompt-muted">至少需要两个不同版本才能比较。</p>
            )}
          </section>

          <section className="prompt-panel">
            <div className="prompt-panel-heading">
              <strong>新建草稿版本</strong>
              <span>提交后生成不可变的新版本</span>
            </div>
            <textarea
              className="prompt-draft-editor"
              value={draftTemplate}
              onChange={(event) => setDraftTemplate(event.target.value)}
              placeholder="输入 Prompt 模板正文，例如：请根据 {context} 回答 {question}"
              rows={7}
            />
            {draftError && <p className="prompt-error">{draftError}</p>}
            <div className="prompt-form-footer">
              <span className="prompt-muted">
                模板变量使用标准变量占位符格式。
              </span>
              <button
                className="prompt-primary-button"
                disabled={!draftTemplate.trim() || createDraft.isPending}
                onClick={() => createDraft.mutate()}
                type="button"
              >
                {createDraft.isPending ? '正在创建…' : '创建草稿版本'}
              </button>
            </div>
          </section>

          <section className="prompt-gate-note">
            <strong>评测 Gate · v{gateVersion ?? '—'}</strong>
            {gateQuery.isPending && gateVersion && <p>正在读取服务端评测记录…</p>}
            {gateQuery.data?.status === 'passed' && (
              <p>
                已找到服务端通过记录（{gateQuery.data.action}），评测运行：{gateQuery.data.eval_run_id}。
              </p>
            )}
            {gateQuery.data?.status === 'not_recorded' && (
              <p>未找到该版本的通过记录；这不代表评测失败，发布操作仍由服务端 Gate 强制校验。</p>
            )}
            {gateQuery.isError && (
              <p>
                {gateQuery.error instanceof Error ? gateQuery.error.message : String(gateQuery.error)}
                。当前后端可能尚未开放 Gate 结果读取接口；不会据此推断评测通过或失败。
              </p>
            )}
            {!gateVersion && <p>暂无版本可查询评测记录。</p>}
            <p>本页面不提供绕过 Gate 的发布操作。</p>
          </section>
        </div>
      </div>
    </section>
  )
}
