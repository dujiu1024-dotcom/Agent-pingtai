import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Search, X } from 'lucide-react'

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
  document: '文档',
}

export function GlobalSearchDialog({ onClose }: { onClose: () => void }) {
  const [term, setTerm] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
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

  useEffect(() => {
    inputRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div
      className="global-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section
        className="global-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="global-search-title"
      >
        <div className="global-dialog-heading">
          <h2 id="global-search-title">全局搜索</h2>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭搜索">
            <X size={18} />
          </button>
        </div>
        <label className="global-dialog-input">
          <Search size={18} aria-hidden="true" />
          <input
            ref={inputRef}
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            placeholder="搜索 Agent、任务、Run、审批…"
            aria-label="全局搜索关键词"
          />
          <kbd>ESC</kbd>
        </label>
        <div className="global-dialog-results" aria-live="polite">
          {term.trim().length < 2 && <p className="detail-empty">输入至少 2 个字符开始搜索。</p>}
          {term.trim().length >= 2 && query.isPending && <LoadingState label="正在搜索" />}
          {query.isError && (
            <ErrorState
              description={query.error instanceof Error ? query.error.message : '搜索失败'}
              onRetry={() => void query.refetch()}
            />
          )}
          {!query.isPending && !query.isError && term.trim().length >= 2 && items.length === 0 && (
            <p className="detail-empty">没有匹配结果。</p>
          )}
          {items.map((item) => (
            <Link
              className="global-search-result"
              key={`${item.type}-${item.id}`}
              to={item.path}
              onClick={onClose}
            >
              <span className="search-result-type">{typeLabels[item.type] ?? item.type}</span>
              <span>
                <strong>{item.title}</strong>
                <small>{item.subtitle}</small>
              </span>
              <span aria-hidden="true">›</span>
            </Link>
          ))}
        </div>
        <div className="global-dialog-footer">
          <span>结果由当前实例搜索服务提供</span>
          <Link to="/search" onClick={onClose}>打开完整搜索页</Link>
        </div>
      </section>
    </div>
  )
}
