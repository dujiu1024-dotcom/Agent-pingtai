import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { ApiError, apiClient } from '@/lib/api'
import { formatDateTime, localizedJson, valueLabel } from '@/lib/i18n/labels'
import './BatchExecutionPage.css'

type BatchTaskResult = {
  task_id?: string
  index?: number
  status?: string
  output?: unknown
  error_type?: string | null
  error_message?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
}

type BatchJob = {
  batch_id: string
  template_id: string
  status: string
  total: number
  succeeded: number
  failed: number
  max_concurrency: number
  results?: BatchTaskResult[]
  dataset?: Record<string, unknown>[]
  error?: string | null
  created_at?: string | null
  updated_at?: string | null
  started_at?: string | null
  finished_at?: string | null
}

type BatchListResponse = { items: BatchJob[] }

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}

const statusLabel = (value: string) => STATUS_LABELS[value] ?? valueLabel(value)

const errorText = (error: unknown) => {
  if (error instanceof ApiError && error.status === 403)
    return '当前账号没有批次执行权限（需要批次读取或模板执行权限）。'
  if (error instanceof ApiError && error.status === 503)
    return '批次服务未启用，或依赖暂不可用。'
  if (error instanceof ApiError && error.status === 404)
    return '批次不存在，可能已被清理。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

const isActive = (status: string) => status === 'queued' || status === 'running'

export function BatchExecutionPage() {
  const queryClient = useQueryClient()
  const [templateId, setTemplateId] = useState('')
  const [datasetText, setDatasetText] = useState('')
  const [maxConcurrency, setMaxConcurrency] = useState(4)
  const [formError, setFormError] = useState('')
  const [formMessage, setFormMessage] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [rowError, setRowError] = useState('')

  const batchesQuery = useQuery({
    queryKey: ['batches', 'list'],
    queryFn: () =>
      apiClient.get<BatchListResponse>('/platform/v1/workbench/batches?limit=100'),
    refetchInterval: 5000,
    retry: false,
  })

  const detailQuery = useQuery({
    queryKey: ['batches', 'detail', selectedId],
    enabled: Boolean(selectedId),
    queryFn: () =>
      apiClient.get<BatchJob>(
        `/platform/v1/workbench/batches/${encodeURIComponent(selectedId)}`,
      ),
    retry: false,
  })

  const submitBatch = useMutation({
    mutationFn: (payload: {
      template_id: string
      dataset: Record<string, unknown>[]
      max_concurrency: number
    }) => apiClient.post<BatchJob>('/platform/v1/workbench/batches', payload),
    onSuccess: async (data) => {
      setFormError('')
      setFormMessage(`批次 ${data.batch_id} 已提交，共 ${data.total} 条任务。`)
      await queryClient.invalidateQueries({ queryKey: ['batches'] })
    },
    onError: (error) => {
      setFormMessage('')
      setFormError(errorText(error))
    },
  })

  const cancelBatch = useMutation({
    mutationFn: (batchId: string) =>
      apiClient.post<BatchJob>(
        `/platform/v1/workbench/batches/${encodeURIComponent(batchId)}/cancel`,
      ),
    onSuccess: async () => {
      setRowError('')
      await queryClient.invalidateQueries({ queryKey: ['batches'] })
    },
    onError: (error) => setRowError(errorText(error)),
  })

  const retryBatch = useMutation({
    mutationFn: (batchId: string) =>
      apiClient.post<BatchJob>(
        `/platform/v1/workbench/batches/${encodeURIComponent(batchId)}/retry`,
        { idempotency_key: crypto.randomUUID() },
      ),
    onSuccess: async () => {
      setRowError('')
      await queryClient.invalidateQueries({ queryKey: ['batches'] })
    },
    onError: (error) => setRowError(errorText(error)),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setFormMessage('')
    setFormError('')
    if (!templateId.trim()) {
      setFormError('请填写模板 ID。')
      return
    }
    let parsed: unknown
    try {
      parsed = JSON.parse(datasetText)
    } catch {
      setFormError('数据集不是合法的 JSON，请检查括号、引号与逗号。')
      return
    }
    if (!Array.isArray(parsed) || parsed.length === 0) {
      setFormError('数据集必须是非空的对象数组（每行一个输入对象）。')
      return
    }
    if (
      parsed.some(
        (row) => row === null || typeof row !== 'object' || Array.isArray(row),
      )
    ) {
      setFormError('数据集的每一行都必须是 JSON 对象。')
      return
    }
    if (
      !Number.isInteger(maxConcurrency) ||
      maxConcurrency < 1 ||
      maxConcurrency > 1000
    ) {
      setFormError('最大并发必须是 1 到 1000 之间的整数。')
      return
    }
    submitBatch.mutate({
      template_id: templateId.trim(),
      dataset: parsed as Record<string, unknown>[],
      max_concurrency: maxConcurrency,
    })
  }

  const confirmCancel = (batch: BatchJob) => {
    if (!window.confirm(`取消批次 ${batch.batch_id}？正在执行的任务会停止。`)) return
    cancelBatch.mutate(batch.batch_id)
  }

  const batches = batchesQuery.data?.items ?? []
  const selected = detailQuery.data

  return (
    <section className="page-content batch-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">工作空间 / 批次执行</p>
          <h1>批次执行</h1>
          <p>提交模板化批次任务，跟踪进度并处理失败行。列表每 5 秒自动刷新。</p>
        </div>
        <small className="batch-refresh-note">自动刷新 · 5s</small>
      </div>

      <form className="batch-form" onSubmit={submit}>
        <div className="batch-form-row">
          <label>
            模板 ID
            <input
              value={templateId}
              onChange={(event) => setTemplateId(event.target.value)}
              placeholder="例如：agent-task"
              maxLength={200}
            />
          </label>
          <label>
            最大并发
            <input
              type="number"
              min={1}
              max={1000}
              value={maxConcurrency}
              onChange={(event) => setMaxConcurrency(Number(event.target.value))}
            />
          </label>
        </div>
        <label>
          数据集（JSON 对象数组）
          <textarea
            value={datasetText}
            onChange={(event) => setDatasetText(event.target.value)}
            rows={8}
            placeholder={'[\n  {"task_id": "示例输入字段"},\n  {"task_id": "示例输入字段"}\n]'}
            spellCheck={false}
          />
        </label>
        {formError && (
          <p className="batch-form-error" role="alert">
            {formError}
          </p>
        )}
        {formMessage && (
          <p className="batch-form-success" role="status">
            {formMessage}
          </p>
        )}
        <div className="batch-form-actions">
          <button
            className="button-primary"
            type="submit"
            disabled={submitBatch.isPending}
          >
            {submitBatch.isPending ? '提交中…' : '提交批次'}
          </button>
          <span className="batch-form-hint">
            数据集必须是 JSON 对象数组；并发由服务端执行器限制。
          </span>
        </div>
      </form>

      {rowError && (
        <p className="batch-form-error" role="alert">
          {rowError}
        </p>
      )}

      {batchesQuery.isPending && <LoadingState label="正在加载批次" />}

      {batchesQuery.isError && (
        <ErrorState
          title="批次列表加载失败"
          description={errorText(batchesQuery.error)}
          onRetry={() => void batchesQuery.refetch()}
        />
      )}

      {batchesQuery.isSuccess && batches.length === 0 && (
        <EmptyState
          title="暂无批次"
          description="填写模板 ID 与数据集后提交，批次作业会显示在这里。"
        />
      )}

      {batchesQuery.isSuccess && batches.length > 0 && (
        <div className="batch-layout">
          <section className="batch-list" aria-label="批次列表">
            <header>
              <h2>批次列表</h2>
              <span>共 {batches.length} 个</span>
            </header>
            <div className="batch-table-wrap">
              <table className="batch-table">
                <thead>
                  <tr>
                    <th>批次</th>
                    <th>模板</th>
                    <th>状态</th>
                    <th>进度</th>
                    <th>创建时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {batches.map((batch) => (
                    <tr
                      key={batch.batch_id}
                      className={batch.batch_id === selectedId ? 'selected' : ''}
                      onClick={() => setSelectedId(batch.batch_id)}
                    >
                      <td>
                        <strong>{batch.batch_id}</strong>
                        {batch.error && <small className="batch-row-error">{batch.error}</small>}
                      </td>
                      <td>{batch.template_id}</td>
                      <td>
                        <span className={`batch-status status-${batch.status}`}>
                          {statusLabel(batch.status)}
                        </span>
                      </td>
                      <td>
                        {batch.succeeded} / {batch.total}
                        {batch.failed > 0 && ` · 失败 ${batch.failed}`}
                      </td>
                      <td>{formatDateTime(batch.created_at)}</td>
                      <td>
                        <div className="batch-row-actions">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation()
                              setSelectedId(batch.batch_id)
                            }}
                          >
                            查看
                          </button>
                          <button
                            type="button"
                            disabled={!isActive(batch.status) || cancelBatch.isPending}
                            onClick={(event) => {
                              event.stopPropagation()
                              confirmCancel(batch)
                            }}
                          >
                            取消
                          </button>
                          <button
                            type="button"
                            disabled={isActive(batch.status) || retryBatch.isPending}
                            onClick={(event) => {
                              event.stopPropagation()
                              retryBatch.mutate(batch.batch_id)
                            }}
                          >
                            重试
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <aside className="batch-detail" aria-label="批次详情">
            <header>
              <h2>批次详情</h2>
              {selected && (
                <span className={`batch-status status-${selected.status}`}>
                  {statusLabel(selected.status)}
                </span>
              )}
            </header>

            {!selectedId && (
              <p className="batch-detail-hint">点击批次行查看完整数据集与任务结果。</p>
            )}

            {selectedId && detailQuery.isPending && <LoadingState label="正在加载详情" />}

            {selectedId && detailQuery.isError && (
              <ErrorState
                title="批次详情加载失败"
                description={errorText(detailQuery.error)}
                onRetry={() => void detailQuery.refetch()}
              />
            )}

            {selectedId && selected && (
              <>
                <dl className="batch-detail-meta">
                  <div>
                    <dt>批次 ID</dt>
                    <dd>{selected.batch_id}</dd>
                  </div>
                  <div>
                    <dt>模板</dt>
                    <dd>{selected.template_id}</dd>
                  </div>
                  <div>
                    <dt>任务总数</dt>
                    <dd>{selected.total}</dd>
                  </div>
                  <div>
                    <dt>成功 / 失败</dt>
                    <dd>
                      {selected.succeeded} / {selected.failed}
                    </dd>
                  </div>
                  <div>
                    <dt>开始时间</dt>
                    <dd>{formatDateTime(selected.started_at)}</dd>
                  </div>
                  <div>
                    <dt>结束时间</dt>
                    <dd>{formatDateTime(selected.finished_at)}</dd>
                  </div>
                </dl>

                {selected.results && selected.results.length > 0 && (
                  <div className="batch-result-list">
                    <h3>任务结果</h3>
                    <ul>
                      {selected.results.map((result, index) => (
                        <li key={result.task_id ?? `${result.index ?? index}`}>
                          <span>#{result.index ?? index}</span>
                          <span className={`batch-status status-${result.status ?? 'unknown'}`}>
                            {statusLabel(result.status ?? '')}
                          </span>
                          <span>{result.duration_ms ?? '—'} ms</span>
                          {result.error_message && (
                            <small>{result.error_message}</small>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <details className="batch-detail-json">
                  <summary>查看完整详情</summary>
                  <pre>{localizedJson(selected)}</pre>
                </details>
              </>
            )}
          </aside>
        </div>
      )}
    </section>
  )
}
