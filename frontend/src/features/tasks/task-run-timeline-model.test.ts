import { describe, expect, it } from 'vitest'

import type { RunEvent, TaskRecord } from './task-model'
import { buildTaskRunTimelineEntries } from './task-run-timeline-model'

const task = (taskId: string): TaskRecord => ({
  task_id: taskId,
  status: 'running',
})

const event = (eventId: string, occurredAt: string): RunEvent => ({
  event_id: eventId,
  type: 'run.started',
  occurred_at: occurredAt,
})

describe('buildTaskRunTimelineEntries', () => {
  it('associates each persisted event with its task and Run and sorts by occurrence time', () => {
    const runs = [
      { task: task('task-a'), runId: 'run-a' },
      { task: task('task-b'), runId: 'run-b' },
    ]
    const result = buildTaskRunTimelineEntries(runs, [
      [event('event-late', '2026-11-22T10:02:00Z')],
      [event('event-early', '2026-11-22T10:01:00Z')],
    ])

    expect(
      result.map(({ event: item, task: owner, runId }) => [
        item.event_id,
        owner.task_id,
        runId,
      ]),
    ).toEqual([
      ['event-early', 'task-b', 'run-b'],
      ['event-late', 'task-a', 'run-a'],
    ])
  })

  it('does not invent events when a Run event snapshot is empty or absent', () => {
    const result = buildTaskRunTimelineEntries(
      [{ task: task('task-a'), runId: 'run-a' }],
      [[]],
    )
    expect(result).toEqual([])
    expect(buildTaskRunTimelineEntries([], [])).toEqual([])
  })

  it('uses deterministic Run and event identifiers when timestamps tie', () => {
    const sameTime = '2026-11-22T10:00:00Z'
    const result = buildTaskRunTimelineEntries(
      [
        { task: task('task-b'), runId: 'run-b' },
        { task: task('task-a'), runId: 'run-a' },
      ],
      [
        [event('event-b', sameTime)],
        [event('event-a', sameTime)],
      ],
    )
    expect(result.map(({ runId }) => runId)).toEqual(['run-a', 'run-b'])
  })
})
