import { describe, expect, it } from 'vitest'
import { getOfficeDeskPosition } from './office-desk-layout'

describe('getOfficeDeskPosition', () => {
  it('maps the first, middle, and last desks to the established 7x3 grid', () => {
    expect(getOfficeDeskPosition('desk-01')).toEqual({ x: -7.2, z: -4.6 })
    expect(getOfficeDeskPosition('desk-08')).toEqual({ x: -7.2, z: -0.09999999999999964 })
    expect(getOfficeDeskPosition('desk-21')).toEqual({ x: 6.9, z: 4.4 })
  })

  it('maps every valid desk to a unique deterministic position', () => {
    const positions = Array.from({ length: 21 }, (_, index) =>
      getOfficeDeskPosition(`desk-${String(index + 1).padStart(2, '0')}`),
    )

    expect(positions).toHaveLength(21)
    expect(new Set(positions.map((position) => `${position?.x},${position?.z}`)).size).toBe(21)
    expect(positions.every((position) => position !== undefined)).toBe(true)
  })

  it.each([null, undefined, '', 'desk-0', 'desk-22', 'desk-1', 'DESK-01', 'desk-01-extra', 'agent-01'])(
    'returns undefined for invalid desk ID %s',
    (deskId) => {
      expect(getOfficeDeskPosition(deskId)).toBeUndefined()
    },
  )
})
