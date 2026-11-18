import { describe, expect, it } from 'vitest'
import { formatDuration, sortEvents, unwrapList } from './task-model'
describe('task command center model', () => {
  it('normalizes list envelopes', () => {
    expect(unwrapList({ data: [1] })).toEqual([1])
    expect(unwrapList({ items: [2] })).toEqual([2])
    expect(unwrapList(null)).toEqual([])
  })
  it('sorts events by sequence', () => {
    expect(
      sortEvents([
        { event_id: 'b', type: 'x', sequence: 2 },
        { event_id: 'a', type: 'x', sequence: 1 },
      ]).map((x) => x.event_id),
    ).toEqual(['a', 'b'])
  })
  it('formats known durations', () => {
    expect(formatDuration(65000)).toBe('1m 5s')
    expect(formatDuration()).toBe('—')
  })
})
