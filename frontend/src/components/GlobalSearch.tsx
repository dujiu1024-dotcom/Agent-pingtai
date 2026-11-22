import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'

interface SearchItem {
  type: string
  id: string
  title: string
  subtitle: string
  path: string
}

const typeLabels: Record<string, string> = {
  agent: 'Agent',
  task: '任务',
  run: '运行',
  approval: '审批',
}

export function GlobalSearch() {
  const [term, setTerm] = useState('')
  const query = useQuery({
    queryKey: ['global-search', term.trim()],
    queryFn: () =>
      apiClient.get<{ items: SearchItem[]; count: number }>(
        `/platform/v1/search?q=${encodeURIComponent(term.trim())}&limit=30`,
      ),
    enabled: term.trim().length >= 2,
    retry: false,
  })
  const items = query.data?.items ?? []

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">搜索</p>
        <h1>全局搜索</h1>
        <p>按名称或 ID 检索 Agent、任务、运行与审批（至少 2 个字符）。</p>
      </div>
      <label className="search-input">
        <span aria-hidden="true">⌕</span>
        <input
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="搜索 Agent、任务、Run、审批…"
          aria-label="全局搜索关键词"
          autoFocus
        />
      </label>
      {term.trim().length < 2 && (
        <p className="detail-empty">输入至少 2 个字符开始搜索。</p>
      )}
      {term.trim().length >= 2 && query.isPending && <LoadingState />}
      {query.isError && (
        <ErrorState
          description={
            query.error instanceof Error ? query.error.message : '搜索失败'
          }
          onRetry={() => void query.refetch()}
        />
      )}
      {!query.isPending && !query.isError && term.trim().length >= 2 && items.length === 0 && (
        <p className="detail-empty">没有匹配结果。</p>
      )}
      {items.length > 0 && (
        <ul className="search-results">
          {items.map((item) => (
            <li key={`${item.type}-${item.id}`}>
              <Link className="search-result" to={item.path}>
                <span className="search-result-type">
                  {typeLabels[item.type] ?? item.type}
                </span>
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.subtitle}</small>
                </span>
                <span aria-hidden="true">›</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
