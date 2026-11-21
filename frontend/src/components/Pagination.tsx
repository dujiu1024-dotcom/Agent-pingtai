interface PaginationProps {
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange?: (pageSize: number) => void
  pageSizeOptions?: number[]
}

export function Pagination({
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 20, 50, 100],
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(Math.max(1, page), totalPages)
  const start = total === 0 ? 0 : (safePage - 1) * pageSize + 1
  const end = Math.min(safePage * pageSize, total)

  // 页码按钮：当前页前后各 1 个，共 3-5 个
  const pages: (number | '…')[] = []
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pages.push(i)
  } else {
    pages.push(1)
    if (safePage > 3) pages.push('…')
    const from = Math.max(2, safePage - 1)
    const to = Math.min(totalPages - 1, safePage + 1)
    for (let i = from; i <= to; i++) pages.push(i)
    if (safePage < totalPages - 2) pages.push('…')
    pages.push(totalPages)
  }

  return (
    <nav className="pagination" aria-label="分页">
      <span className="pagination-info">
        第 {start}–{end} 条 / 共 {total} 条
      </span>
      <div className="pagination-actions">
        <button
          type="button"
          className="pagination-btn"
          disabled={safePage <= 1}
          onClick={() => onPageChange(safePage - 1)}
        >
          上一页
        </button>
        {pages.map((p, i) =>
          p === '…' ? (
            <span key={`e${i}`} className="pagination-ellipsis">…</span>
          ) : (
            <button
              key={p}
              type="button"
              className="pagination-btn"
              data-active={p === safePage}
              onClick={() => onPageChange(p)}
            >
              {p}
            </button>
          ),
        )}
        <button
          type="button"
          className="pagination-btn"
          disabled={safePage >= totalPages}
          onClick={() => onPageChange(safePage + 1)}
        >
          下一页
        </button>
        {onPageSizeChange && (
          <select
            className="pagination-select"
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
          >
            {pageSizeOptions.map((n) => (
              <option key={n} value={n}>{n} 条/页</option>
            ))}
          </select>
        )}
      </div>
    </nav>
  )
}
