import type { ReactNode } from 'react'

export type FieldType =
  | 'text'
  | 'textarea'
  | 'number'
  | 'boolean'
  | 'select'
  | 'json'
  | 'file'

export type FormValues = Record<string, string | number | boolean>
export type Row = Record<string, unknown>

export interface FieldDef {
  key: string
  label: string
  type?: FieldType
  required?: boolean
  placeholder?: string
  help?: string
  options?: Array<{ value: string; label: string }>
  defaultValue?: string | number | boolean
  /** 仅新建表单展示 */
  createOnly?: boolean
  /** 仅编辑表单展示 */
  editOnly?: boolean
  rows?: number
}

export interface SubmitContext {
  values: Record<string, unknown>
  row?: Row
  file?: File | null
}

export interface WriteSpec {
  label?: string
  submit: (context: SubmitContext) => Promise<unknown>
  confirm?: string
}

export interface RowActionSpec {
  label: string
  visible?: (row: Row) => boolean
  confirm?: string
  run?: (row: Row) => Promise<unknown>
  link?: (row: Row) => string
}

export interface RemoveSpec {
  label?: string
  confirm?: string
  run: (row: Row) => Promise<unknown>
}

export interface CrudConfig {
  title: string
  description: string
  /** 单条记录的称呼，用于弹窗标题 */
  entityLabel?: string
  listPath: string
  /** 自定义列表加载（用于合并多个接口的数据） */
  load?: () => Promise<unknown>
  queryKey?: string
  idField: string
  fields: FieldDef[]
  columns?: string[]
  columnLabels?: Record<string, string>
  create?: WriteSpec
  update?: WriteSpec
  remove?: RemoveSpec
  rowActions?: RowActionSpec[]
  emptyHint?: string
  note?: ReactNode
  /** 客户端筛选：按字段分组的快速筛选栏 */
  filters?: CrudFilter[]
}

export interface CrudFilterGroup {
  label: string
  test: (value: unknown) => boolean
}

export interface CrudFilter {
  key: string
  label: string
  groups: CrudFilterGroup[]
}

export function visibleFields(
  fields: FieldDef[],
  editing: boolean,
): FieldDef[] {
  return fields.filter((field) =>
    editing ? !field.createOnly : !field.editOnly,
  )
}

export function initialValues(
  fields: FieldDef[],
  row?: Row | null,
): FormValues {
  const values: FormValues = {}
  for (const field of fields) {
    const raw = row ? row[field.key] : undefined
    const value = raw === undefined || raw === null ? field.defaultValue : raw
    if (field.type === 'boolean') {
      values[field.key] = Boolean(value)
    } else if (field.type === 'number') {
      values[field.key] =
        typeof value === 'number'
          ? value
          : value === undefined
            ? ''
            : Number(value)
    } else if (field.type === 'json') {
      values[field.key] =
        value === undefined || value === null
          ? ''
          : typeof value === 'string'
            ? value
            : JSON.stringify(value, null, 2)
    } else if (field.type === 'file') {
      values[field.key] = ''
    } else {
      values[field.key] =
        value === undefined || value === null ? '' : String(value)
    }
  }
  return values
}

export function parseValues(
  fields: FieldDef[],
  values: FormValues,
): Record<string, unknown> {
  const parsed: Record<string, unknown> = {}
  for (const field of fields) {
    if (field.type === 'file') continue
    const value = values[field.key]
    if (field.type === 'json') {
      const text = String(value ?? '').trim()
      if (!text) {
        parsed[field.key] = {}
        continue
      }
      try {
        parsed[field.key] = JSON.parse(text) as unknown
      } catch {
        throw new Error(`${field.label}不是合法的 JSON`)
      }
    } else if (field.type === 'number') {
      if (value === '' || value === undefined) {
        parsed[field.key] = undefined
      } else {
        parsed[field.key] = Number(value)
      }
    } else if (field.type === 'boolean') {
      parsed[field.key] = Boolean(value)
    } else {
      parsed[field.key] = value === undefined ? '' : String(value)
    }
    if (
      field.required &&
      (parsed[field.key] === '' || parsed[field.key] === undefined)
    ) {
      throw new Error(`${field.label}为必填项`)
    }
  }
  return parsed
}
