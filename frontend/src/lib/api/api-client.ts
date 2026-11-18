import type {
  ApiClientOptions,
  ApiErrorEnvelope,
  ApiRequestOptions,
  PageEnvelope,
  PaginationParams,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId?: string
  readonly details?: unknown
  readonly retryable: boolean

  constructor(
    message: string,
    options: {
      status: number
      code: string
      requestId?: string
      details?: unknown
      retryable?: boolean
    },
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status
    this.code = options.code
    this.requestId = options.requestId
    this.details = options.details
    this.retryable = options.retryable ?? false
  }
}

export class ApiConflictError extends ApiError {
  constructor(message: string, options: ConstructorParameters<typeof ApiError>[1]) {
    super(message, options)
    this.name = 'ApiConflictError'
  }
}

interface CachedResponse {
  etag: string
  body: unknown
}

function isBodyInit(value: unknown): value is BodyInit {
  return (
    typeof value === 'string' ||
    value instanceof Blob ||
    value instanceof FormData ||
    value instanceof URLSearchParams ||
    value instanceof ArrayBuffer ||
    ArrayBuffer.isView(value) ||
    value instanceof ReadableStream
  )
}

function makeIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function appendQuery(url: URL, params: Record<string, unknown>): void {
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (typeof value === 'object') {
      url.searchParams.set(key, JSON.stringify(value))
    } else {
      url.searchParams.set(key, String(value))
    }
  }
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly timeoutMs: number
  private readonly fetcher: typeof fetch
  private readonly defaultHeaders: HeadersInit
  private readonly credentials: RequestCredentials
  private readonly etagCache = new Map<string, CachedResponse>()

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? '/api'
    this.timeoutMs = options.timeoutMs ?? 15_000
    this.fetcher = options.fetcher ?? fetch
    this.defaultHeaders = options.defaultHeaders ?? {}
    this.credentials = options.credentials ?? 'same-origin'
  }

  async request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    const {
      body,
      timeoutMs = this.timeoutMs,
      idempotencyKey,
      ifMatch,
      etag = true,
      headers: requestHeaders,
      signal: callerSignal,
      method: inputMethod = 'GET',
      ...rest
    } = options
    const method = inputMethod.toUpperCase()
    const url = new URL(path.replace(/^\//, ''), this.resolveBaseUrl())
    const headers = new Headers(this.defaultHeaders)
    new Headers(requestHeaders).forEach((value, key) => headers.set(key, value))
    headers.set('Accept', 'application/json')
    const cacheKey = `${method}:${url.toString()}`
    const cached = method === 'GET' && etag ? this.etagCache.get(cacheKey) : undefined
    if (cached) headers.set('If-None-Match', cached.etag)

    let requestBody: BodyInit | null | undefined
    if (body === null || body === undefined) {
      requestBody = body
    } else if (isBodyInit(body)) {
      requestBody = body
    } else {
      headers.set('Content-Type', 'application/json')
      requestBody = JSON.stringify(body)
    }

    if (ifMatch) headers.set('If-Match', ifMatch)

    if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(method)) {
      headers.set('Idempotency-Key', idempotencyKey ?? makeIdempotencyKey())
    } else if (idempotencyKey) {
      headers.set('Idempotency-Key', idempotencyKey)
    }

    const controller = new AbortController()
    const onCallerAbort = () => controller.abort(callerSignal?.reason)
    if (callerSignal?.aborted) onCallerAbort()
    else callerSignal?.addEventListener('abort', onCallerAbort, { once: true })
    const timeout = setTimeout(
      () => controller.abort(new DOMException('Request timed out', 'TimeoutError')),
      timeoutMs,
    )

    try {
      const response = await this.fetcher(url, {
        ...rest,
        method,
        headers,
        body: requestBody,
        credentials: this.credentials,
        signal: controller.signal,
      })

      if (response.status === 304 && cached) return cached.body as T

      const requestId = response.headers.get('x-request-id') ?? undefined
      const responseBody = await this.readBody(response)
      if (!response.ok) {
        const error = this.toError(response.status, responseBody, requestId)
        if (response.status === 409 || response.status === 412) {
          throw new ApiConflictError(error.message, {
            status: error.status,
            code: error.code,
            requestId: error.requestId,
            details: error.details,
            retryable: error.retryable,
          })
        }
        throw error
      }

      if (method === 'GET' && etag) {
        const responseEtag = response.headers.get('etag')
        if (responseEtag) {
          this.etagCache.set(cacheKey, { etag: responseEtag, body: responseBody })
        }
      }
      return responseBody as T
    } catch (error) {
      if (controller.signal.aborted) {
        if (controller.signal.reason instanceof DOMException &&
            controller.signal.reason.name === 'TimeoutError') {
          throw new ApiError('Request timed out', {
            status: 408,
            code: 'REQUEST_TIMEOUT',
            retryable: true,
          })
        }
        throw error
      }
      throw error
    } finally {
      clearTimeout(timeout)
      callerSignal?.removeEventListener('abort', onCallerAbort)
    }
  }

  get<T>(path: string, options: Omit<ApiRequestOptions, 'method' | 'body'> = {}) {
    return this.request<T>(path, { ...options, method: 'GET' })
  }

  post<T>(
    path: string,
    body?: ApiRequestOptions['body'],
    options: Omit<ApiRequestOptions, 'method' | 'body'> = {},
  ) {
    return this.request<T>(path, { ...options, method: 'POST', body })
  }

  patch<T>(
    path: string,
    body?: ApiRequestOptions['body'],
    options: Omit<ApiRequestOptions, 'method' | 'body'> = {},
  ) {
    return this.request<T>(path, { ...options, method: 'PATCH', body })
  }

  put<T>(
    path: string,
    body?: ApiRequestOptions['body'],
    options: Omit<ApiRequestOptions, 'method' | 'body'> = {},
  ) {
    return this.request<T>(path, { ...options, method: 'PUT', body })
  }

  delete<T>(path: string, options: Omit<ApiRequestOptions, 'method'> = {}) {
    return this.request<T>(path, { ...options, method: 'DELETE' })
  }

  list<T>(
    path: string,
    params: PaginationParams = {},
    options: Omit<ApiRequestOptions, 'method' | 'body'> = {},
  ): Promise<PageEnvelope<T>> {
    const url = new URL(path.replace(/^\\//, ''), this.resolveBaseUrl())
    appendQuery(url, {
      page: params.page,
      page_size: params.page_size,
      cursor: params.cursor,
      sort: params.sort,
      filters: params.filters,
    })
    return this.get<PageEnvelope<T>>(url.toString(), {
      ...options,
      etag: options.etag ?? true,
    })
  }

  private resolveBaseUrl(): string {
    if (typeof window === 'undefined') return `http://localhost${this.baseUrl.endsWith('/') ? this.baseUrl : `${this.baseUrl}/`}`
    return new URL(this.baseUrl.endsWith('/') ? this.baseUrl : `${this.baseUrl}/`, window.location.origin).toString()
  }

  private async readBody(response: Response): Promise<unknown> {
    if (response.status === 204) return undefined
    const text = await response.text()
    if (!text) return undefined
    try {
      return JSON.parse(text) as unknown
    } catch {
      return text
    }
  }

  private toError(status: number, payload: unknown, requestId?: string): ApiError {
    const envelope = payload as Partial<ApiErrorEnvelope> | null
    return new ApiError(envelope?.message ?? `Request failed with status ${status}`, {
      status,
      code: envelope?.code ?? 'HTTP_ERROR',
      requestId: envelope?.request_id ?? requestId,
      details: envelope?.details,
      retryable: envelope?.retryable ?? status >= 500,
    })
  }
}

export const apiClient = new ApiClient()
