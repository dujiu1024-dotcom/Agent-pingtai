import { describe, expect, it } from 'vitest'

import { normalizeHomeTrendPoints } from './Dashboard'

describe('normalizeHomeTrendPoints', () => {
  it('normalizes the three task trend series from the API payload', () => {
    expect(normalizeHomeTrendPoints({
      data: { points: [
        { date: '2026-11-20', today_count: 12, completed_count: 8, in_progress_count: 4 },
      ] },
    })).toEqual([
      { label: '11/20', today: 12, completed: 8, inProgress: 4 },
    ])
  })

  it('returns no fabricated points for an empty response', () => {
    expect(normalizeHomeTrendPoints({ data: [] })).toEqual([])
  })

  it('preserves explicit zero values', () => {
    expect(normalizeHomeTrendPoints([
      { label: '今天', task_count: 0, completed_count: 0, running: 0 },
    ])).toEqual([
      { label: '今天', today: 0, completed: 0, inProgress: 0 },
    ])
  })
})
