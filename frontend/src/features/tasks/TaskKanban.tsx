import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { ReactNode } from 'react'

type KanbanTask = { task_id: string; status: string }
type Props<T extends KanbanTask> = {
  statuses: string[]
  tasks: T[]
  renderCard: (task: T) => ReactNode
  statusLabel: (status: string) => string
}

const ROW_HEIGHT = 182
const VIEWPORT_HEIGHT = 546
const WINDOW_OVERSCAN = 3

function parseList(value: string | null): string[] {
  if (!value) return []
  try {
    const parsed: unknown = JSON.parse(value)
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

export function TaskKanban<T extends KanbanTask>({
  statuses,
  tasks,
  renderCard,
  statusLabel,
}: Props<T>) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [scrollPositions, setScrollPositions] = useState<Record<string, number>>({})
  const configuredColumns = parseList(searchParams.get('kanban_columns'))
  const hiddenColumns = new Set(parseList(searchParams.get('kanban_hidden')))
  const savedOrder = parseList(searchParams.get('kanban_order'))
  const columns = useMemo(() => {
    const available = statuses.filter((status) => !hiddenColumns.has(status))
    const preferred = configuredColumns.filter((status) => available.includes(status))
    const remaining = available.filter((status) => !preferred.includes(status))
    return [...preferred, ...remaining]
  }, [statuses, searchParams])
  const orderedTasks = useMemo(() => {
    const rank = new Map(savedOrder.map((id, index) => [id, index]))
    return [...tasks].sort((a, b) => {
      const aRank = rank.get(a.task_id)
      const bRank = rank.get(b.task_id)
      if (aRank == null && bRank == null) return a.task_id.localeCompare(b.task_id)
      if (aRank == null) return 1
      if (bRank == null) return -1
      return aRank - bRank
    })
  }, [tasks, savedOrder.join('|')])

  const updateParam = (key: string, value: string[]) => {
    const next = new URLSearchParams(searchParams)
    if (value.length) next.set(key, JSON.stringify(value))
    else next.delete(key)
    setSearchParams(next, { replace: true })
  }

  const reorderColumns = (source: string, target: string) => {
    if (source === target) return
    const next = [...columns]
    const from = next.indexOf(source)
    const to = next.indexOf(target)
    if (from < 0 || to < 0) return
    next.splice(from, 1)
    next.splice(to, 0, source)
    updateParam('kanban_columns', next)
  }

  const reorderTasks = (status: string, sourceId: string, targetId: string) => {
    if (sourceId === targetId) return
    const inColumn = orderedTasks.filter((task) => task.status === status)
    const ids = inColumn.map((task) => task.task_id)
    const from = ids.indexOf(sourceId)
    const to = ids.indexOf(targetId)
    if (from < 0 || to < 0) return
    ids.splice(from, 1)
    ids.splice(to, 0, sourceId)
    const otherIds = orderedTasks.filter((task) => task.status !== status).map((task) => task.task_id)
    updateParam('kanban_order', [...otherIds, ...ids])
  }

  const toggleColumn = (status: string, visible: boolean) => {
    const next = new Set(hiddenColumns)
    if (visible) next.delete(status)
    else next.add(status)
    updateParam('kanban_hidden', [...next])
  }

  return (
    <section className="task-kanban-shell" aria-label="任务看板">
      <details className="task-kanban-config">
        <summary>看板列配置（{columns.length}/{statuses.length}）</summary>
        <div role="group" aria-label="显示的看板状态列">
          {statuses.map((status) => (
            <label key={status}>
              <input
                type="checkbox"
                checked={!hiddenColumns.has(status)}
                onChange={(event) => toggleColumn(status, event.target.checked)}
              />
              {statusLabel(status)}
            </label>
          ))}
          <button type="button" onClick={() => {
            const next = new URLSearchParams(searchParams)
            next.delete('kanban_hidden')
            next.delete('kanban_columns')
            setSearchParams(next, { replace: true })
          }}>恢复默认列</button>
        </div>
      </details>
      <div className="task-kanban">
        {columns.map((status) => {
          const items = orderedTasks.filter((task) => task.status === status)
          const scrollTop = scrollPositions[status] ?? 0
          const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - WINDOW_OVERSCAN)
          const visibleCount = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + WINDOW_OVERSCAN * 2
          const end = Math.min(items.length, start + visibleCount)
          const visibleItems = items.slice(start, end)
          const topSpace = start * ROW_HEIGHT
          const bottomSpace = Math.max(0, (items.length - end) * ROW_HEIGHT)
          return (
            <section
              className="task-column"
              key={status}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                const source = event.dataTransfer.getData('text/task-status')
                const target = event.dataTransfer.getData('text/task-column')
                if (source && target === 'column') reorderColumns(source, status)
              }}
            >
              <header
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.setData('text/task-status', status)
                  event.dataTransfer.setData('text/task-column', 'column')
                  event.dataTransfer.effectAllowed = 'move'
                }}
                title="拖动以调整列顺序"
              >
                <strong>{statusLabel(status)}</strong>
                <span>{items.length}</span>
                <span className="task-kanban-drag-hint" aria-hidden="true">⠿</span>
              </header>
              <div
                className="task-kanban-viewport"
                style={{ height: VIEWPORT_HEIGHT }}
                onScroll={(event) => setScrollPositions((current) => ({
                  ...current,
                  [status]: event.currentTarget.scrollTop,
                }))}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault()
                  const sourceId = event.dataTransfer.getData('text/task-card')
                  const sourceStatus = event.dataTransfer.getData('text/task-card-status')
                  const targetId = event.dataTransfer.getData('text/task-card-target')
                  if (sourceId && sourceStatus === status && targetId) reorderTasks(status, sourceId, targetId)
                }}
              >
                <div style={{ height: topSpace }} aria-hidden="true" />
                {visibleItems.map((task) => (
                  <div
                    className="task-kanban-card-slot"
                    key={task.task_id}
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.setData('text/task-card', task.task_id)
                      event.dataTransfer.setData('text/task-card-status', status)
                      event.dataTransfer.effectAllowed = 'move'
                    }}
                    onDragOver={(event) => {
                      event.preventDefault()
                      event.dataTransfer.setData('text/task-card-target', task.task_id)
                    }}
                    onDrop={(event) => {
                      event.preventDefault()
                      const sourceId = event.dataTransfer.getData('text/task-card')
                      const sourceStatus = event.dataTransfer.getData('text/task-card-status')
                      if (sourceId && sourceStatus === status) reorderTasks(status, sourceId, task.task_id)
                    }}
                  >
                    {renderCard(task)}
                  </div>
                ))}
                <div style={{ height: bottomSpace }} aria-hidden="true" />
                {!items.length && <p className="task-kanban-empty">此状态暂无任务</p>}
              </div>
            </section>
          )
        })}
      </div>
    </section>
  )
}
