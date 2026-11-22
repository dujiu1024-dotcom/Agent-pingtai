import { describe, expect, it } from 'vitest'
import { createBlackboardMessagePayload } from './blackboard-message'

describe('Blackboard message payload', () => {
  it('creates a note in the explicitly selected scope without client-supplied identity', () => {
    expect(createBlackboardMessagePayload({
      scope: 'run', scopeId: 'run-123', title: '进度同步', message: '研究阶段已完成',
    })).toEqual({
      scope: 'run', scope_id: 'run-123', kind: 'note',
      content: { title: '进度同步', message: '研究阶段已完成' }, source_refs: [],
    })
  })

  it('trims input and supplies a default title', () => {
    expect(createBlackboardMessagePayload({
      scope: 'project', scopeId: ' project-1 ', title: ' ', message: '  已提交初步结论  ',
    }).content).toEqual({ title: '协作消息', message: '已提交初步结论' })
  })

  it('rejects missing or unsupported scope and empty message', () => {
    expect(() => createBlackboardMessagePayload({ scope: '', scopeId: 'x', title: '', message: 'm' })).toThrow()
    expect(() => createBlackboardMessagePayload({ scope: 'organization', scopeId: 'x', title: '', message: 'm' })).toThrow()
    expect(() => createBlackboardMessagePayload({ scope: 'task', scopeId: ' ', title: '', message: 'm' })).toThrow()
    expect(() => createBlackboardMessagePayload({ scope: 'task', scopeId: 'x', title: '', message: ' ' })).toThrow()
  })
})
