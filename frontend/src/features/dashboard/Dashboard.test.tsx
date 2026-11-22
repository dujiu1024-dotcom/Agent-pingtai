import { describe, expect, it } from 'vitest'

import { normalizeDashboardTrendPoints } from './Dashboard'

describe('normalizeDashboardTrendPoints', () => {
  it('normalizes task counts and success rate from the API payload', () => {
    expect(
      normalizeDashboardTrendPoints({
        data: {
          points: [
            { date: '2026-11-20', task_count: 12, success_rate: 0.75 },
          ],
        },
      }),
    ).toEqual([{ label: '11/20', tasks: 12, success: 0.75 }])
  })

  it('returns no fabricated points for an empty response', () => {
    expect(normalizeDashboardTrendPoints({ data: [] })).toEqual([])
  })

  it('preserves explicit zero values', () => {
    expect(
      normalizeDashboardTrendPoints([
        { label: '今天', task_count: 0, success_rate: 0 },
      ]),
    ).toEqual([{ label: '今天', tasks: 0, success: 0 }])
  })
})
