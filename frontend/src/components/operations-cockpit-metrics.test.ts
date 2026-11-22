import { describe, expect, it } from 'vitest'
import { buildTaskDateDrilldown, terminalTaskOutcome } from './operations-cockpit-metrics'

describe('terminalTaskOutcome', () => {
  it('uses success and failure terminal states only', () => {
    expect(terminalTaskOutcome([
      { status: 'completed' }, { status: 'failed' }, { status: 'cancelled' },
      { status: 'running' }, { status: 'waiting_approval' }, { status: 'unknown' },
    ])).toEqual({ successCount: 1, failureCount: 1, sampleSize: 2, rate: 50 })
  })
  it('does not invent a zero rate without terminal samples', () => {
    expect(terminalTaskOutcome([{ status: 'queued' }, { status: 'cancelled' }])).toEqual({
      successCount: 0, failureCount: 0, sampleSize: 0, rate: undefined,
    })
  })
})

describe('buildTaskDateDrilldown', () => {
  it('uses TaskCommandCenter date filters', () => {
    expect(buildTaskDateDrilldown('2026-11-22')).toBe('/workspace/tasks?from=2026-11-22T00%3A00&to=2026-11-22T23%3A59')
  })
  it('rejects malformed dates', () => {
    expect(buildTaskDateDrilldown('invalid')).toBe('/workspace/tasks')
  })
})
