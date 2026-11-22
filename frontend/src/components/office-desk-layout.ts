/**
 * Deterministic world-space layout for the 21 desks in the Office scene.
 *
 * Coordinates intentionally mirror the existing OfficeWorkspace desk grid:
 * seven columns, three rows, centered around the scene origin. This module
 * has no React, Three.js, browser, or WebGL dependency.
 */
export type OfficeDeskPosition = Readonly<{ x: number; z: number }>

const DESK_COUNT = 21
const COLUMN_COUNT = 7
const FIRST_DESK_X = -7.2
const DESK_X_STEP = 2.35
const FIRST_DESK_Z = -4.6
const DESK_Z_STEP = 4.5

/**
 * Resolve a stable desk ID (desk-01 through desk-21) to its scene position.
 * Invalid, missing, or out-of-range IDs return undefined rather than
 * inventing a fallback position.
 */
export function getOfficeDeskPosition(
  deskId: string | null | undefined,
): OfficeDeskPosition | undefined {
  if (typeof deskId !== 'string') return undefined

  const match = /^desk-(\d{2})$/.exec(deskId)
  if (!match) return undefined

  const deskNumber = Number(match[1])
  if (!Number.isInteger(deskNumber) || deskNumber < 1 || deskNumber > DESK_COUNT) {
    return undefined
  }

  const index = deskNumber - 1
  return {
    x: FIRST_DESK_X + (index % COLUMN_COUNT) * DESK_X_STEP,
    z: FIRST_DESK_Z + Math.floor(index / COLUMN_COUNT) * DESK_Z_STEP,
  }
}
