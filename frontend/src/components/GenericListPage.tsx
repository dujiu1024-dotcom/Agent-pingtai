import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'

const dt = (v?: string | number): string => {
  if (!v) return '—'
  try {
    return new Date(String(v)).toLocaleString()
  } catch {
    return String(v)
  }
}

function toList(raw: unknown): unknown[] {
  if (Array.isArray(raw)) return raw
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>
    for (const key of ['items', 'data', 'list', 'results', 'records', 'rows']) {
      if (Array.isArray(obj[key])) return obj[key]
    }
    // 单对象包装
    return [obj]
  }
  return []
}

function cell(v: unknown): ReactNode {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? '✓' : '—'
  if (typeof v === 'number') return v
  if (typeof v === 'string') {
    // 时间戳检测
    if (/^\d{4}-\d{2}-\d{2}[T ]/.test(v) || /\d{10,13}/.test(v)) {
      const d = new Date(v)
      if (!isNaN(d.getTime())) return dt(v)
    }
    return v.length > 80 ? v.slice(0, 80) + '…' : v
  }
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'object') {
    try {
      return <pre className="cell-json">{JSON.stringify(v, null, 2)}</pre>
    } catch {
      return '—'
    }
  }
  return String(v)
}

export interface GenericListPageProps {
  title: string
  description: string
  apiPath: string
  /** queryKey 前缀，默认取 apiPath */
  queryKey?: string
  /** 自动刷新毫秒数，0 不刷新 */
  refetchInterval?: number
}

/**
 * 通用列表页：接任意后端 API，自动识别 items/data/list/results/records/rows 包装
 * 渲染 Loading / Error / Empty / Data 四态，表格列自动按对象 key 生成
 */
export function GenericListPage(props: GenericListPageProps) {
  const {
    title,
    description,
    apiPath,
    queryKey = apiPath,
    refetchInterval = 0,
  } = props
  const q = useQuery({
    queryKey: [queryKey],
    queryFn: () => apiClient.get<unknown>(apiPath),
    refetchInterval: refetchInterval || undefined,
  })

  if (q.isPending) return <LoadingState label={`正在加载${title}`} />
  if (q.isError)
    return (
      <section className="page-content">
        <ErrorState
          title={`${title}加载失败`}
          description={
            q.error instanceof Error
              ? `${q.error.message} (API: ${apiPath})`
              : String(q.error)
          }
          onRetry={() => void q.refetch()}
        />
      </section>
    )

  const rows = toList(q.data)

  return (
    <section className="page-content">
      <div className="page-heading">
        <h1>{title}</h1>
        <p>{description}</p>
        <small>
          来源 <code>{apiPath}</code> · 返回 {rows.length} 条
        </small>
      </div>
      {rows.length === 0 ? (
        <EmptyState title={`暂无${title}`} description="服务端未返回记录。" />
      ) : (
        <div className="generic-table-wrap">
          <table className="generic-table">
            <thead>
              <tr>
                {Object.keys(rows[0] as Record<string, unknown>).map((k) => (
                  <th key={k}>{k}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {Object.entries(row as Record<string, unknown>).map(([k, v]) => (
                    <td key={k}>{cell(v)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
