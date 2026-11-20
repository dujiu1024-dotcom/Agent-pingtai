import { describe, expect, it } from 'vitest'

import { safePromptId } from './prompt-registry-utils'

describe('safePromptId', () => {
  it('accepts bounded prompt identifiers', () => {
    expect(safePromptId('support.reply')).toBe(true)
    expect(safePromptId('team:prompt_v2')).toBe(true)
  })

  it('rejects empty, path-like and oversized identifiers', () => {
    expect(safePromptId('')).toBe(false)
    expect(safePromptId('../secret')).toBe(false)
    expect(safePromptId('a'.repeat(129))).toBe(false)
  })
})
