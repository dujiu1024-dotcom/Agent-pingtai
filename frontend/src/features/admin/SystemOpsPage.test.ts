import { describe, expect, it } from 'vitest'

import { getQueryFailureDetails, systemOpsEndpoints } from './SystemOpsPage'

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

describe('system operations API contracts', () => {
  it('uses production Metrics, Health and Scheduler endpoints', () => {
    expect(systemOpsEndpoints).toEqual({
      metrics: '/platform/v1/observability/metrics/json',
      capacity: '/platform/v1/observability/health/capacity',
      readiness: '../health/ready',
      jobs: '/platform/v1/scheduler/jobs',
      runs: '/platform/v1/scheduler/runs?limit=20',
    })
  })

  it.each([
    ['Metrics', 'METRICS_UNAVAILABLE'],
    ['容量健康', 'CAPACITY_HEALTH_UNAVAILABLE'],
    ['Readiness', 'READINESS_UNAVAILABLE'],
    ['Scheduler 作业列表', 'SCHEDULER_JOBS_UNAVAILABLE'],
    ['Scheduler 执行记录', 'SCHEDULER_RUNS_UNAVAILABLE'],
  ])('preserves HTTP failure diagnostics for %s', (label, code) => {
    const error = Object.assign(new Error(`${label} endpoint unavailable`), {
      status: 503,
      code,
      requestId: 'req-ops-failure',
    })

    expect(getQueryFailureDetails(label, error)).toEqual({
      title: `${label}读取失败`,
      message: `${label} endpoint unavailable`,
      status: 503,
      code,
      requestId: 'req-ops-failure',
    })
  })
})
