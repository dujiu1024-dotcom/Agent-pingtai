import { describe, expect, it } from 'vitest'

import { parseModelDefinition } from './ModelsManagementPage'

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
