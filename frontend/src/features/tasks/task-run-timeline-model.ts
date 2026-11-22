import type { RunEvent, TaskRecord } from './task-model'

export type TaskRunTimelineSource = {
  task: TaskRecord
  runId: string
}

export type TaskRunTimelineEntry = {
  task: TaskRecord
  runId: string
  event: RunEvent
}

/** Flatten persisted event snapshots and order them by event occurrence time. */
export function buildTaskRunTimelineEntries(
  runs: TaskRunTimelineSource[],
  eventLists: RunEvent[][],
): TaskRunTimelineEntry[] {
  return eventLists
    .flatMap((events, index) => {
      const run = runs[index]
      if (!run) return []
      return events.map((event) => ({ task: run.task, runId: run.runId, event }))
    })
    .sort(
      (a, b) =>
        (Date.parse(a.event.occurred_at ?? '') || 0) -
          (Date.parse(b.event.occurred_at ?? '') || 0) ||
        a.runId.localeCompare(b.runId) ||
        a.event.event_id.localeCompare(b.event.event_id),
    )
}
