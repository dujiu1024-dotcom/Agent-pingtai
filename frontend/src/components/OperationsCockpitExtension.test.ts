import { describe, expect, it } from 'vitest'

import { filterUsageEvents } from './OperationsCockpitExtension'

const events = [
  { event_id: '1', run_id: 'r1', project_id: 'p1', agent_id: 'a1', model_key: 'm1', input_tokens: 2, output_tokens: 3, total_tokens: 5, cost_usd: '0.1', occurred_at: '2026-11-22T00:00:00Z' },
  { event_id: '2', run_id: 'r2', project_id: 'p2', agent_id: 'a2', model_key: 'm2', input_tokens: 4, output_tokens: 5, total_tokens: 9, cost_usd: '0.2', occurred_at: '2026-11-22T01:00:00Z' },
]

describe('filterUsageEvents', () => {
  it('returns all authorized events when no dimensions are selected', () => {
    expect(filterUsageEvents(events, '', '', '')).toHaveLength(2)
  })
  it('applies project, agent and model filters conjunctively', () => {
    expect(filterUsageEvents(events, 'p1', 'a1', 'm1').map((event) => event.event_id)).toEqual(['1'])
    expect(filterUsageEvents(events, 'p1', 'a2', '').map((event) => event.event_id)).toEqual([])
  })
  it('does not include events with missing dimension values in a selected filter', () => {
    expect(filterUsageEvents([{ ...events[0], project_id: null }], 'p1', '', '')).toEqual([])
  })
})
