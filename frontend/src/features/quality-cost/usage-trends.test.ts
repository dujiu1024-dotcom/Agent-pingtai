import { describe, expect, it } from 'vitest'

import { buildDailyUsageTrend } from './usage-trends'

describe('buildDailyUsageTrend', () => {
  it('groups ledger records by UTC date and sums token and priced cost fields', () => {
    const trend = buildDailyUsageTrend([
      { occurred_at: '2026-11-22T14:00:00Z', input_tokens: 12, output_tokens: 8, total_tokens: 20, cost_usd: '0.125' },
      { occurred_at: '2026-11-22T02:00:00Z', input_tokens: 3, output_tokens: 2, total_tokens: 5, cost_usd: '0.025' },
      { occurred_at: '2026-11-21T23:30:00Z', input_tokens: 7, output_tokens: 1, total_tokens: 8, cost_usd: '0.05' },
    ])
    expect(trend).toEqual([
      { date: '2026-11-21', inputTokens: 7, outputTokens: 1, totalTokens: 8, costUsd: 0.05, pricedEvents: 1, unpricedEvents: 0 },
      { date: '2026-11-22', inputTokens: 15, outputTokens: 10, totalTokens: 25, costUsd: 0.15, pricedEvents: 2, unpricedEvents: 0 },
    ])
  })

  it('does not turn missing or invalid prices into zero-cost events', () => {
    const trend = buildDailyUsageTrend([
      { occurred_at: '2026-11-22T10:00:00Z', input_tokens: 10, output_tokens: 5, cost_usd: '' },
      { occurred_at: '2026-11-22T11:00:00Z', input_tokens: 1, output_tokens: 2, cost_usd: 'unknown' },
    ])
    expect(trend[0]).toMatchObject({ totalTokens: 18, costUsd: null, pricedEvents: 0, unpricedEvents: 2 })
  })

  it('ignores invalid timestamps and sorts dates chronologically', () => {
    const trend = buildDailyUsageTrend([
      { occurred_at: 'not-a-date', total_tokens: 999, cost_usd: '9' },
      { occurred_at: '2026-11-23T00:00:00Z', total_tokens: 2, cost_usd: '0.2' },
      { occurred_at: '2026-11-20T00:00:00Z', total_tokens: 1, cost_usd: '0.1' },
    ])
    expect(trend.map((day) => day.date)).toEqual(['2026-11-20', '2026-11-23'])
  })

  it('returns an empty result for an empty ledger', () => {
    expect(buildDailyUsageTrend([])).toEqual([])
  })

  it('aggregates very large finite token and cost values without truncating the result', () => {
    const trend = buildDailyUsageTrend([
      {
        occurred_at: '2026-11-22T10:00:00Z',
        input_tokens: 4_000_000_000_000_000,
        output_tokens: 1_000_000_000_000_000,
        total_tokens: 5_000_000_000_000_000,
        cost_usd: '1e150',
      },
      {
        occurred_at: '2026-11-22T11:00:00Z',
        input_tokens: 2_000_000_000_000_000,
        output_tokens: 0,
        total_tokens: 2_000_000_000_000_000,
        cost_usd: '2e150',
      },
    ])

    expect(trend).toEqual([
      {
        date: '2026-11-22',
        inputTokens: 6_000_000_000_000_000,
        outputTokens: 1_000_000_000_000_000,
        totalTokens: 7_000_000_000_000_000,
        costUsd: 3e150,
        pricedEvents: 2,
        unpricedEvents: 0,
      },
    ])
  })

  it('preserves negative adjustments instead of clamping them to zero', () => {
    const trend = buildDailyUsageTrend([
      {
        occurred_at: '2026-11-22T10:00:00Z',
        input_tokens: 10,
        output_tokens: 5,
        total_tokens: 15,
        cost_usd: '0.75',
      },
      {
        occurred_at: '2026-11-22T11:00:00Z',
        input_tokens: -4,
        output_tokens: -1,
        total_tokens: -5,
        cost_usd: '-1.25',
      },
    ])

    expect(trend).toEqual([
      {
        date: '2026-11-22',
        inputTokens: 6,
        outputTokens: 4,
        totalTokens: 10,
        costUsd: -0.5,
        pricedEvents: 2,
        unpricedEvents: 0,
      },
    ])
  })
})
