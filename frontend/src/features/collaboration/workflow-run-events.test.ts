import { describe, expect, it } from 'vitest'
import {
  isNodeRuntimeEvent,
  mergeRunRuntimeEvents,
  nodeStatusForRuntimeEvent,
  normalizeRunSseEvent,
} from './workflow-run-events'

describe('Workflow Run live event adapter', () => {
  it('normalizes broker SSE envelopes and keeps node payload fields', () => {
    const event = normalizeRunSseEvent(
      { run_id: 'run-a', sequence: 7, event_type: 'STEP_START', node_id: 'research', node_name: 'Research' },
      'run-a:7',
      '2026-11-22T00:00:00Z',
    )
    expect(event).toMatchObject({
      event_id: 'run-a:7',
      event_type: 'STEP_START',
      sequence: 7,
      node_id: 'research',
      payload: { node_id: 'research', node_name: 'Research' },
    })
  })

  it('merges snapshot and replayed events by broker ID and sorts by sequence', () => {
    const first = { event_id: 'run-a:1', sequence: 1, event_type: 'RUN_START' }
    const second = { event_id: 'run-a:2', sequence: 2, event_type: 'STEP_START' }
    expect(mergeRunRuntimeEvents([second], [first, second])).toEqual([first, second])
  })

  it('recognizes LangGraph step events and maps their actual lifecycle states', () => {
    const payload = { node_id: 'research' }
    expect(isNodeRuntimeEvent('STEP_START', 'research', payload)).toBe(true)
    expect(nodeStatusForRuntimeEvent('STEP_START', payload)).toBe('running')
    expect(nodeStatusForRuntimeEvent('STEP_FINISH', payload)).toBe('completed')
    expect(nodeStatusForRuntimeEvent('STEP_ERROR', payload)).toBe('failed')
    expect(nodeStatusForRuntimeEvent('STEP_RETRY', payload)).toBe('retrying')
    expect(isNodeRuntimeEvent('RUN_START', '', {})).toBe(false)
  })

  it('does not fabricate a runtime event for reconnect control frames', () => {
    expect(normalizeRunSseEvent({ event_type: 'RECONNECT_REQUIRED' }, '')).toBeNull()
    expect(normalizeRunSseEvent({ message: 'missing type' }, '')).toBeNull()
  })
})
