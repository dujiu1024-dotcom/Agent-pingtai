export type TaskOutcome = {
  successCount: number
  failureCount: number
  sampleSize: number
  rate?: number
}

const SUCCESS_STATUSES = new Set(['completed', 'succeeded', 'success', 'done'])
const FAILURE_STATUSES = new Set(['failed', 'failure', 'error', 'timeout', 'timed_out'])

/** Calculate success rate from terminal success/failure samples only. */
export function terminalTaskOutcome(tasks: Array<{ status?: unknown }>): TaskOutcome {
  let successCount = 0
  let failureCount = 0
  for (const task of tasks) {
    const status = String(task.status ?? '').trim().toLowerCase()
    if (SUCCESS_STATUSES.has(status)) successCount += 1
    else if (FAILURE_STATUSES.has(status)) failureCount += 1
  }
  const sampleSize = successCount + failureCount
  return { successCount, failureCount, sampleSize, rate: sampleSize ? (successCount / sampleSize) * 100 : undefined }
}

/** Link a local calendar day to TaskCommandCenter's created-at from/to filters. */
export function buildTaskDateDrilldown(day: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return '/workspace/tasks'
  const params = new URLSearchParams({ from: `${day}T00:00`, to: `${day}T23:59` })
  return `/workspace/tasks?${params.toString()}`
}
