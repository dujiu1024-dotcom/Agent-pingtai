import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import { displayValue, fieldLabel, localizedJson } from '@/lib/i18n/labels'
import { Modal, RecordForm } from './Modal'
import { initialValues, parseValues, visibleFields } from './types'
import type { CrudConfig, FormValues, Row, RowActionSpec } from './types'
import './crud.css'

function toList(raw: unknown): Row[] {
  if (Array.isArray(raw)) return raw as Row[]
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>
    for (const key of ['items', 'data', 'list', 'results', 'records', 'rows']) {
      if (Array.isArray(obj[key])) return obj[key] as Row[]
    }
    return [obj]
  }
  return []
}

function cell(key: string, value: unknown) {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) {
    if (value.every((item) => typeof item !== 'object')) {
      return value.map((item) => displayValue(key, item)).join('、') || '—'
    }
    return (
      <details className="cell-json-wrap">
        <summary>{`展开（${value.length} 项）`}</summary>
        <pre className="cell-json">{localizedJson(value)}</pre>
      </details>
    )
  }
  if (typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>)
    return (
      <details className="cell-json-wrap">
        <summary>{`展开（${keys.length} 个字段）`}</summary>
        <pre className="cell-json">{localizedJson(value)}</pre>
      </details>
    )
  }
  const text = displayValue(key, value)
  return text.length > 120 ? `${text.slice(0, 120)}…` : text
}

export function CrudListPage({ config }: { config: CrudConfig }) {
  const queryClient = useQueryClient()
  const queryKey = config.queryKey ?? config.listPath
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Row | null>(null)
  const [values, setValues] = useState<FormValues>({})
  const [file, setFile] = useState<File | null>(null)
  const [formError, setFormError] = useState('')
  // 每个 filter 对应一个 active 索引
  const [activeFilters, setActiveFilters] = useState<Record<string, number>>(() => {
    const init: Record<string, number> = {}
    config.filters?.forEach((f) => { init[f.key] = 0 })
    return init
  })
  // 分页：客户端分页（每页 10 条）
  const PAGE_SIZE = 10
  const [page, setPage] = useState(0)

  const list = useQuery({
    queryKey: ['crud', queryKey],
    queryFn: () =>
      config.load ? config.load() : apiClient.get<unknown>(config.listPath),
    retry: false,
  })

  const mutation = useMutation({
    mutationFn: (task: () => Promise<unknown>) => task(),
    onSuccess: () => {
      closeModal()
      void queryClient.invalidateQueries({ queryKey: ['crud', queryKey] })
    },
  })

  const rows = toList(list.data)
  // 应用客户端 filters
  const filteredRows = config.filters
    ? rows.filter((row) =>
        config.filters!.every((filter) => {
          const activeIdx = activeFilters[filter.key] ?? 0
          const group = filter.groups[activeIdx]
          if (!group) return true
          const cell = row[filter.key]
          return group.test(cell)
        }),
      )
    : rows
  // 客户端分页
  const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages - 1)
  const pagedRows = filteredRows.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)
  // 切换 filter 时重置 page
  useEffect(() => { setPage(0) }, [activeFilters])
  const columns = config.columns ?? (rows[0] ? Object.keys(rows[0]) : [])
  const modalOpen = creating || editing !== null
  const editingMode = editing !== null
  const activeFields = visibleFields(config.fields, editingMode)
  const writeSpec = editingMode ? config.update : config.create
  const formId = 'crud-record-form'

  function openCreate() {
    setEditing(null)
    setValues(initialValues(visibleFields(config.fields, false)))
    setFile(null)
    setFormError('')
    setCreating(true)
  }

  function openEdit(row: Row) {
    setCreating(false)
    setEditing(row)
    setValues(initialValues(visibleFields(config.fields, true), row))
    setFile(null)
    setFormError('')
  }

  function closeModal() {
    setCreating(false)
    setEditing(null)
    setFile(null)
    setFormError('')
  }

  function submitForm() {
    setFormError('')
    if (!writeSpec) return
    try {
      const parsed = parseValues(activeFields, values)
      mutation.mutate(() =>
        writeSpec.submit({
          values: parsed,
          row: editing ?? undefined,
          file,
        }),
      )
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '表单填写有误')
    }
  }

  function runAction(spec: RowActionSpec, row: Row) {
    if (!spec.run) return
    if (spec.confirm && !window.confirm(spec.confirm)) return
    mutation.mutate(() => spec.run!(row))
  }

  function removeRow(row: Row) {
    if (!config.remove) return
    if (
      !window.confirm(
        config.remove.confirm ?? '确认删除这条记录？此操作不可撤销。',
      )
    ) {
      return
    }
    mutation.mutate(() => config.remove!.run(row))
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <h1>{config.title}</h1>
        <p>{config.description}</p>
        <small>
          服务端数据 · 共 {rows.length} 条
          {config.create ? ' · 可在本页新建与修改' : ' · 当前接口只读'}
        </small>
      </div>
      {config.note}
      <div className="crud-toolbar">
        {config.create && (
          <button className="button-primary" type="button" onClick={openCreate}>
            {config.create.label ?? '新建'}
          </button>
        )}
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

      {/* 客户端筛选栏 */}
      {config.filters && config.filters.length > 0 && rows.length > 0 && (
        <div className="crud-filter-bar">
          {config.filters.map((filter) => (
            <div key={filter.key} className="crud-filter-group">
              <span className="crud-filter-label">{filter.label}</span>
              {filter.groups.map((group, idx) => {
                const count = rows.filter((row) => group.test(row[filter.key])).length
                const active = (activeFilters[filter.key] ?? 0) === idx
                return (
                  <button
                    key={idx}
                    type="button"
                    className={`crud-filter-chip${active ? ' active' : ''}`}
                    onClick={() =>
                      setActiveFilters((prev) => ({ ...prev, [filter.key]: idx }))
                    }
                  >
                    {group.label}
                    <span className="crud-filter-count">{count}</span>
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}

      {list.isPending && <LoadingState label={`正在加载${config.title}`} />}
      {list.isError && (
        <ErrorState
          title={`${config.title}加载失败`}
          description={
            list.error instanceof Error
              ? `${list.error.message} (API: ${config.listPath})`
              : String(list.error)
          }
          onRetry={() => void list.refetch()}
        />
      )}
      {list.data !== undefined && filteredRows.length === 0 && (
        <EmptyState
          title={`暂无${config.title}`}
          description={config.emptyHint ?? '服务端未返回记录。'}
          action={
            config.create ? (
              <button
                className="button-primary"
                type="button"
                onClick={openCreate}
              >
                {config.create.label ?? '新建'}
              </button>
            ) : undefined
          }
        />
      )}
      {filteredRows.length > 0 && (
        <div className="generic-table-wrap">
          <table className="generic-table">
            <thead>
              <tr>
                {columns.map((key) => (
                  <th key={key}>
                    {config.columnLabels?.[key] ?? fieldLabel(key)}
                  </th>
                ))}
                {(config.update ||
                  config.remove ||
                  (config.rowActions?.length ?? 0) > 0) && <th>操作</th>}
              </tr>
            </thead>
            <tbody>
              {pagedRows.map((row, index) => {
                const rowKey = String(row[config.idField] ?? index)
                return (
                  <tr key={rowKey}>
                    {columns.map((key) => (
                      <td
                        key={key}
                        className={
                          key.endsWith('_at') || key.endsWith('_id')
                            ? 'cell-nowrap'
                            : undefined
                        }
                      >
                        {cell(key, row[key])}
                      </td>
                    ))}
                    {(config.update ||
                      config.remove ||
                      (config.rowActions?.length ?? 0) > 0) && (
                      <td className="cell-nowrap crud-actions">
                        {config.update && (
                          <button
                            className="text-link"
                            type="button"
                            onClick={() => openEdit(row)}
                          >
                            编辑
                          </button>
                        )}
                        {config.rowActions?.map((spec) => {
                          const visible = spec.visible
                            ? spec.visible(row)
                            : true
                          if (!visible) return null
                          if (spec.link) {
                            return (
                              <Link
                                className="text-link"
                                key={spec.label}
                                to={spec.link(row)}
                              >
                                {spec.label}
                              </Link>
                            )
                          }
                          return (
                            <button
                              className="text-link"
                              type="button"
                              key={spec.label}
                              disabled={mutation.isPending}
                              onClick={() => runAction(spec, row)}
                            >
                              {spec.label}
                            </button>
                          )
                        })}
                        {config.remove && (
                          <button
                            className="text-link text-link--danger"
                            type="button"
                            onClick={() => removeRow(row)}
                          >
                            {config.remove.label ?? '删除'}
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
          {/* 客户端分页控件 */}
          {totalPages > 1 && (
            <div className="crud-pagination">
              <span className="crud-pagination-info">
                共 {filteredRows.length} 条 · {safePage + 1} / {totalPages} 页
              </span>
              <div className="crud-pagination-controls">
                <button
                  className="crud-page-btn"
                  type="button"
                  disabled={safePage === 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  上一页
                </button>
                {/* 页码按钮：最多显示 5 个，当前页居中 */}
                {(() => {
                  const start = Math.max(0, Math.min(safePage - 2, totalPages - 5))
                  const end = Math.min(totalPages, start + 5)
                  const pages = Array.from({ length: end - start }, (_, i) => start + i)
                  return pages.map((p) => (
                    <button
                      key={p}
                      type="button"
                      className={`crud-page-btn${p === safePage ? ' active' : ''}`}
                      onClick={() => setPage(p)}
                    >
                      {p + 1}
                    </button>
                  ))
                })()}
                <button
                  className="crud-page-btn"
                  type="button"
                  disabled={safePage >= totalPages - 1}
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                >
                  下一页
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {modalOpen && writeSpec && (
        <Modal
          title={`${editingMode ? '编辑' : '新建'}${config.entityLabel ?? config.title}`}
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
                {mutation.isPending ? '提交中…' : (writeSpec.label ?? '保存')}
              </button>
            </>
          }
        >
          <RecordForm
            formId={formId}
            fields={activeFields}
            values={values}
            onChange={(key, value) =>
              setValues((current) => ({ ...current, [key]: value }))
            }
            onFileChange={setFile}
            onSubmit={submitForm}
          />
        </Modal>
      )}
    </section>
  )
}
