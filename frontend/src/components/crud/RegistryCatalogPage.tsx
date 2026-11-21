import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import {
  resolveNames,
  useRegistryCaches,
  type RegistryNameRow,
} from '@/lib/registry-names'
import { valueLabel, formatDateTime } from '@/lib/i18n/labels'
import { Modal } from './Modal'
import './crud.css'

export interface RegistryExtraColumn {
  key: string
  label: string
  render: (definition: Record<string, unknown>, extra?: Record<string, unknown>) => React.ReactNode
}

export interface RegistryKindConfig {
  kind: string
  title: string
  description: string
  itemLabel: string
  detailPath: (itemId: string) => string
  template: Record<string, unknown>
  extraColumns?: RegistryExtraColumn[]
}

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

export function RegistryCatalogPage({
  config,
}: {
  config: RegistryKindConfig
}) {
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<RegistryItem | null>(null)
  const [itemId, setItemId] = useState('')
  const [name, setName] = useState('')
  const [definition, setDefinition] = useState('')
  const [publish, setPublish] = useState(true)
  const [formError, setFormError] = useState('')
  const [busy, setBusy] = useState(false)

  const { agentNames, toolNames } = useRegistryCaches()

  const list = useQuery({
    queryKey: ['registry', config.kind],
    queryFn: () =>
      apiClient.get<{ items: RegistryItem[] }>(
        `/platform/v1/registry/${config.kind}`,
      ),
    retry: false,
  })

  const mutation = useMutation({
    mutationFn: (task: () => Promise<unknown>) => task(),
    onSuccess: () => {
      closeModal()
      void queryClient.invalidateQueries({
        queryKey: ['registry', config.kind],
      })
    },
  })

  const items = list.data?.items ?? []
  const modalOpen = creating || editing !== null
  const formId = 'registry-record-form'

  function openCreate() {
    setEditing(null)
    setItemId('')
    setName('')
    setDefinition(JSON.stringify(config.template, null, 2))
    setPublish(true)
    setFormError('')
    setCreating(true)
  }

  async function openEdit(item: RegistryItem) {
    setBusy(true)
    setFormError('')
    try {
      const active = await apiClient.get<RegistryItem>(
        `/platform/v1/registry/${config.kind}/${item.item_id}/active`,
      )
      setEditing(item)
      setItemId(item.item_id)
      setName(String(active.definition.name ?? ''))
      setDefinition(JSON.stringify(active.definition, null, 2))
    } catch {
      setEditing(item)
      setItemId(item.item_id)
      setName(String(item.definition.name ?? ''))
      setDefinition(JSON.stringify(item.definition, null, 2))
    } finally {
      setBusy(false)
      setPublish(true)
      setCreating(true)
    }
  }

  function closeModal() {
    setCreating(false)
    setEditing(null)
    setFormError('')
  }

  function submit() {
    setFormError('')
    const id = itemId.trim()
    if (!id) {
      setFormError(`${config.itemLabel} ID 为必填项`)
      return
    }
    let parsed: unknown
    try {
      parsed = definition.trim() ? JSON.parse(definition) : {}
    } catch {
      setFormError('定义不是合法的 JSON')
      return
    }
    if (
      typeof parsed !== 'object' ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      setFormError('定义必须是 JSON 对象')
      return
    }
    const payload = {
      ...(parsed as Record<string, unknown>),
      name: name.trim() || id,
    }
    mutation.mutate(async () => {
      const created = await apiClient.post<RegistryItem>(
        `/platform/v1/registry/${config.kind}/${id}/versions`,
        { definition: payload },
      )
      if (publish) {
        await apiClient.post(
          `/platform/v1/registry/${config.kind}/${id}/versions/${created.version}/publish`,
        )
      }
      return created
    })
  }

  function disable(item: RegistryItem) {
    if (
      !window.confirm(
        `确认停用 ${item.item_id}？停用后运行时不会再解析到该条目。`,
      )
    ) {
      return
    }
    mutation.mutate(() =>
      apiClient.post(
        `/platform/v1/registry/${config.kind}/${item.item_id}/disable`,
      ),
    )
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <h1>{config.title}</h1>
        <p>{config.description}</p>
        <small>
          注册表 · 共 {items.length} 项 · 保存会生成新版本，发布后才生效
        </small>
      </div>
      <div className="crud-toolbar">
        <button className="button-primary" type="button" onClick={openCreate}>
          新建{config.itemLabel}
        </button>
        <button
          className="button-secondary"
          type="button"
          onClick={() => void list.refetch()}
          disabled={list.isFetching}
        >
          刷新
        </button>
        {mutation.isError && (
          <span className="crud-error" role="alert">
            {mutation.error instanceof Error
              ? mutation.error.message
              : '操作失败'}
          </span>
        )}
      </div>

      {list.isPending && <LoadingState label={`正在加载${config.title}`} />}
      {list.isError && (
        <ErrorState
          title={`${config.title}加载失败`}
          description={
            list.error instanceof Error ? list.error.message : '注册表加载失败'
          }
          onRetry={() => void list.refetch()}
        />
      )}
      {list.data && items.length === 0 && (
        <EmptyState
          title={`暂无${config.title}`}
          description="当前租户还没有注册任何条目。"
          action={
            <button
              className="button-primary"
              type="button"
              onClick={openCreate}
            >
              新建{config.itemLabel}
            </button>
          }
        />
      )}
      {items.length > 0 && (
        <div className="generic-table-wrap">
          <table className="generic-table">
            <thead>
              <tr>
                <th>{config.itemLabel} ID</th>
                <th>名称</th>
                {config.extraColumns?.map((col) => (
                  <th key={col.key}>{col.label}</th>
                ))}
                <th>最新版本</th>
                <th>状态</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                // 给 extra data 做中文名映射
                const extra: Record<string, unknown> = { used_by: item.used_by }
                // tool kind：used_by 是 agent IDs → 中文名
                if (config.kind === 'tool' && item.used_by?.length) {
                  extra.used_by_names = resolveNames(agentNames, item.used_by)
                }
                // skill kind：definition.tool_ids → tool 中文名
                if (config.kind === 'skill') {
                  extra.tool_names = resolveNames(
                    toolNames,
                    (item.definition.tool_ids as unknown[]) ?? [],
                  )
                }
                return (
                <tr key={item.item_id}>
                  <td className="cell-nowrap">{item.item_id}</td>
                  <td>{String(item.definition.name ?? '—')}</td>
                  {config.extraColumns?.map((col) => (
                    <td key={col.key}>{col.render(item.definition, extra)}</td>
                  ))}
                  <td>v{item.version}</td>
                  <td>
                    <span
                      className={`registry-status registry-status--${item.status}`}
                    >
                      {valueLabel(item.status)}
                    </span>
                  </td>
                  <td className="cell-nowrap">
                    {formatDateTime(item.created_at)}
                  </td>
                  <td className="cell-nowrap crud-actions">
                    <button
                      className="text-link"
                      type="button"
                      disabled={busy}
                      onClick={() => void openEdit(item)}
                    >
                      编辑
                    </button>
                    <Link
                      className="text-link"
                      to={config.detailPath(item.item_id)}
                    >
                      详情
                    </Link>
                    {item.status !== 'disabled' && (
                      <button
                        className="text-link text-link--danger"
                        type="button"
                        onClick={() => disable(item)}
                      >
                        停用
                      </button>
                    )}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {modalOpen && (
        <Modal
          title={`${editing ? '编辑' : '新建'}${config.itemLabel}`}
          onClose={closeModal}
          footer={
            <>
              {formError && (
                <span className="crud-error" role="alert">
                  {formError}
                </span>
              )}
              <button
                className="button-secondary"
                type="button"
                onClick={closeModal}
              >
                取消
              </button>
              <button
                className="button-primary"
                type="submit"
                form={formId}
                disabled={mutation.isPending}
              >
                {mutation.isPending
                  ? '提交中…'
                  : publish
                    ? '保存并发布'
                    : '仅保存草稿'}
              </button>
            </>
          }
        >
          <form
            id={formId}
            className="crud-form"
            onSubmit={(event) => {
              event.preventDefault()
              submit()
            }}
          >
            <label className="crud-field" htmlFor={`${formId}-id`}>
              <span>
                {config.itemLabel} ID
                <em className="crud-required">*</em>
              </span>
              <input
                id={`${formId}-id`}
                type="text"
                value={itemId}
                readOnly={editing !== null}
                placeholder="例如 support-agent"
                onChange={(event) => setItemId(event.target.value)}
              />
              <small className="crud-help">
                {editing
                  ? '条目 ID 创建后不可修改。'
                  : '仅允许字母、数字和 . _ : -，创建后不可修改。'}
              </small>
            </label>
            <label className="crud-field" htmlFor={`${formId}-name`}>
              <span>名称</span>
              <input
                id={`${formId}-name`}
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label
              className="crud-field crud-field--wide"
              htmlFor={`${formId}-definition`}
            >
              <span>
                定义（JSON）
                <em className="crud-required">*</em>
              </span>
              <textarea
                id={`${formId}-definition`}
                className="crud-json"
                rows={14}
                value={definition}
                onChange={(event) => setDefinition(event.target.value)}
              />
              <small className="crud-help">
                定义原样提交给注册表；密钥类字段请使用 secret 引用，禁止明文。
              </small>
            </label>
            <label className="crud-field crud-field--wide">
              <span className="crud-checkbox">
                <input
                  type="checkbox"
                  checked={publish}
                  onChange={(event) => setPublish(event.target.checked)}
                />
                <span>保存后立即发布（不勾选则只生成草稿）</span>
              </span>
            </label>
          </form>
        </Modal>
      )}
    </section>
  )
}
