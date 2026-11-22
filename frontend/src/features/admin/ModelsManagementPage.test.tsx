import { describe, expect, it } from 'vitest'

import { getRoutingEmptyState, parseModelDefinition } from './ModelsManagementPage'

describe('model management definition validation', () => {
  it('accepts a JSON object with secret references', () => {
    expect(parseModelDefinition('{"name":"provider-a","secret_ref":"vault://models/a"}')).toEqual({
      name: 'provider-a',
      secret_ref: 'vault://models/a',
    })
  })

  it('rejects arrays and scalar JSON values', () => {
    expect(() => parseModelDefinition('[]')).toThrow('配置必须是 JSON 对象')
    expect(() => parseModelDefinition('"text"')).toThrow('配置必须是 JSON 对象')
  })

  it('rejects secret material at any nesting level', () => {
    expect(() => parseModelDefinition('{"auth":{"api_key":"plain-text"}}')).toThrow('不允许保存密钥明文')
    expect(() => parseModelDefinition('{"clientSecret":"plain-text"}')).toThrow('不允许保存密钥明文')
  })

  it('rejects malformed JSON', () => {
    expect(() => parseModelDefinition('{invalid')).toThrow()
  })
})


describe('model routing empty state', () => {
  it('explains shared-instance behavior when auth is disabled', () => {
    const state = getRoutingEmptyState(false)
    expect(state.title).toContain('共享实例模式')
    expect(state.description).toContain('auth.enabled=false')
    expect(state.description).toContain('不按用户或部门隔离')
    expect(state.description).toContain('不发起策略请求')
  })

  it('includes the requested policy ID in an unavailable detail state', () => {
    const state = getRoutingEmptyState(true, 'policy-primary')
    expect(state.description).toContain('请求的策略 ID：policy-primary')
    expect(state.description).toContain('不展示虚构策略')
  })

  it('keeps the missing routing API explicit when auth is enabled', () => {
    const state = getRoutingEmptyState(true)
    expect(state.title).toContain('API')
    expect(state.description).toContain('routing_policy')
    expect(state.description).toContain('不展示虚构策略')
  })
})
