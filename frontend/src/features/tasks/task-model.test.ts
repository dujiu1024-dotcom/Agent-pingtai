import { describe, expect, it } from 'vitest'
import { filterRunEvents, formatDuration, mergeRunEvents, normalizeTaskListColumns, parseRunEventFrame, sortEvents, unwrapList } from './task-model'
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
  it('keeps the task title column and normalizes URL-configured columns', () => {
    expect(normalizeTaskListColumns(null)).toEqual(['title', 'status', 'priority', 'project', 'agent', 'created', 'run'])
    expect(normalizeTaskListColumns('status,created,unknown,status')).toEqual(['title', 'status', 'created'])
    expect(normalizeTaskListColumns('trace')).toEqual(['title', 'trace'])
  })
  it('parses SSE Run frames and merges replayed events without duplicates', () => {
    const parsed = parseRunEventFrame(
      'id: run-1:2\nevent: token_stream\ndata: {"run_id":"run-1","sequence":2,"event_type":"TOKEN_STREAM","token":"A"}\n\n',
      'run-1',
    )
    expect(parsed).toMatchObject({ event_id: 'run-1:2', type: 'TOKEN_STREAM', sequence: 2 })
    expect(mergeRunEvents(
      [{ event_id: 'run-1:1', type: 'RUN_START', sequence: 1 }],
      [parsed!, parsed!],
    ).map((event) => event.event_id)).toEqual(['run-1:1', 'run-1:2'])
    expect(parseRunEventFrame('data: not-json\n\n', 'run-1')).toBeNull()
  })
  it('filters Run events by exact type and searchable event metadata without mutating input', () => {
    const events = [
      { event_id: 'e-1', type: 'TOOL_CALL', event_type: 'TOOL_CALL', summary: 'Search index', actor: 'agent-alpha', trace_id: 'trace-1' },
      { event_id: 'e-2', type: 'RUN_FAILED', summary: 'Permission denied', source: 'runtime' },
      { event_id: 'e-3', type: 'TOOL_RESULT', summary: 'Index ready', actor: 'agent-beta' },
    ]
    expect(filterRunEvents(events, 'TOOL_CALL').map((event) => event.event_id)).toEqual(['e-1'])
    expect(filterRunEvents(events, 'all', 'TRACE-1').map((event) => event.event_id)).toEqual(['e-1'])
    expect(filterRunEvents(events, 'all', 'permission').map((event) => event.event_id)).toEqual(['e-2'])
    expect(events).toHaveLength(3)
  })
  it('formats known durations', () => {
    expect(formatDuration(65000)).toBe('1m 5s')
    expect(formatDuration()).toBe('—')
  })
})
