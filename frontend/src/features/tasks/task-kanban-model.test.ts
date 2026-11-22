import { describe, expect, it } from 'vitest'
import { reorderKanbanTaskIds } from './task-kanban-model'

type Task = { task_id: string; status: string }

const tasks: Task[] = [
  { task_id: 'todo-a', status: 'todo' },
  { task_id: 'doing-a', status: 'doing' },
  { task_id: 'todo-b', status: 'todo' },
  { task_id: 'done-a', status: 'done' },
  { task_id: 'todo-c', status: 'todo' },
]

describe('reorderKanbanTaskIds', () => {
  it('moves a card before its target within the same status column', () => {
    expect(reorderKanbanTaskIds(tasks, 'todo', 'todo-c', 'todo-a')).toEqual([
      'doing-a', 'done-a', 'todo-c', 'todo-a', 'todo-b',
    ])
  })

  it('moves a card after removing its original position', () => {
    expect(reorderKanbanTaskIds(tasks, 'todo', 'todo-a', 'todo-c')).toEqual([
      'doing-a', 'done-a', 'todo-b', 'todo-c', 'todo-a',
    ])
  })

  it('preserves relative order of cards in other columns', () => {
    const result = reorderKanbanTaskIds(tasks, 'todo', 'todo-b', 'todo-c')
    expect(result?.slice(0, 2)).toEqual(['doing-a', 'done-a'])
  })

  it.each([
    ['same source and target', 'todo-a', 'todo-a'],
    ['missing source', 'missing', 'todo-a'],
    ['missing target', 'todo-a', 'missing'],
    ['empty source', '', 'todo-a'],
    ['empty target', 'todo-a', ''],
  ])('returns null for %s', (_reason, source, target) => {
    expect(reorderKanbanTaskIds(tasks, 'todo', source, target)).toBeNull()
  })

  it('rejects cross-column moves so drag sorting cannot change server status', () => {
    expect(reorderKanbanTaskIds(tasks, 'todo', 'doing-a', 'todo-a')).toBeNull()
  })

  it('does not mutate the input tasks', () => {
    const before = structuredClone(tasks)
    reorderKanbanTaskIds(tasks, 'todo', 'todo-c', 'todo-a')
    expect(tasks).toEqual(before)
  })
})
