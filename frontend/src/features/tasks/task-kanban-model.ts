export type KanbanTaskOrderItem = { task_id: string; status: string }

/**
 * Compute the URL-persisted task order after a same-column drag-and-drop.
 * Returns null for a no-op/invalid drag; never mutates the supplied task list.
 * Cross-column drops are intentionally rejected because they would imply a
 * server-side status transition, which this presentation-only Kanban does not own.
 */
export function reorderKanbanTaskIds<T extends KanbanTaskOrderItem>(
  orderedTasks: readonly T[],
  status: string,
  sourceId: string,
  targetId: string,
): string[] | null {
  if (!sourceId || !targetId || sourceId === targetId) return null

  const columnIds = orderedTasks
    .filter((task) => task.status === status)
    .map((task) => task.task_id)
  const from = columnIds.indexOf(sourceId)
  const to = columnIds.indexOf(targetId)
  if (from < 0 || to < 0) return null

  columnIds.splice(from, 1)
  columnIds.splice(to, 0, sourceId)

  const otherIds = orderedTasks
    .filter((task) => task.status !== status)
    .map((task) => task.task_id)
  return [...otherIds, ...columnIds]
}
