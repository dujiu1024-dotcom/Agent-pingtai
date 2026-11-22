import { describe, expect, it } from 'vitest'

import { getQueryFailureDetails } from './SystemOpsPage'

describe('system operations query failures', () => {
  it('labels a failed data source and preserves HTTP diagnostics', () => {
    const error = Object.assign(new Error('Scheduler API unavailable'), {
      status: 503,
      code: 'SERVICE_UNAVAILABLE',
      requestId: 'req-ops-123',
    })

    expect(getQueryFailureDetails('Scheduler 作业列表', error)).toEqual({
      title: 'Scheduler 作业列表读取失败',
      message: 'Scheduler API unavailable',
      status: 503,
      code: 'SERVICE_UNAVAILABLE',
      requestId: 'req-ops-123',
    })
  })

  it('keeps network failures explicit when no HTTP status is available', () => {
    const error = Object.assign(new Error('Network connection failed'), {
      code: 'NETWORK_ERROR',
    })

    expect(getQueryFailureDetails('Metrics', error)).toEqual({
      title: 'Metrics读取失败',
      message: 'Network connection failed',
      status: undefined,
      code: 'NETWORK_ERROR',
      requestId: undefined,
    })
  })
})
