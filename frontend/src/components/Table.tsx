import type { ReactNode } from 'react'
import { Empty } from './Empty'
import { Loading } from './Loading'

export interface TableColumn<T> {
  key: string
  title: ReactNode
  render?: (row: T, index: number) => ReactNode
  width?: string
  align?: 'start' | 'center' | 'end'
}

export interface TableProps<T> {
  columns: TableColumn<T>[]
  data: T[]
  rowKey: (row: T, index: number) => string
  loading?: boolean
  emptyTitle?: string
  emptyDescription?: string
  density?: 'comfortable' | 'standard' | 'compact'
  caption?: string
}

export function Table<T>({ columns, data, rowKey, loading = false, emptyTitle = '暂无数据', emptyDescription = '当前没有可展示的记录。', density = 'standard', caption }: TableProps<T>) {
  return (
    <div className={`ui-table-wrap ui-table-wrap--${density}`} aria-busy={loading}>
      <table className="ui-table">
        {caption && <caption className="ui-table__caption">{caption}</caption>}
        <thead><tr>{columns.map((column) => (
          <th key={column.key} scope="col" style={{ width: column.width }} className={`is-${column.align ?? 'start'}`}>{column.title}</th>
        ))}</tr></thead>
        <tbody>
          {loading ? <tr><td colSpan={columns.length}><Loading label="正在加载数据" /></td></tr>
            : data.length === 0 ? <tr><td colSpan={columns.length}><Empty title={emptyTitle} description={emptyDescription} /></td></tr>
              : data.map((row, index) => <tr key={rowKey(row, index)}>{columns.map((column) => (
                <td key={column.key} className={`is-${column.align ?? 'start'}`}>
                  {column.render ? column.render(row, index) : String((row as unknown as Record<string, unknown>)[column.key] ?? '—')}
                </td>
              ))}</tr>)}
        </tbody>
      </table>
    </div>
  )
}
