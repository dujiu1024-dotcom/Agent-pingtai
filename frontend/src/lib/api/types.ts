export interface PaginationParams {
  page?: number
  page_size?: number
  cursor?: string
  sort?: string
  filters?: Record<string, string | number | boolean | null>
}

export interface PaginationMeta {
  page: number
  page_size: number
  total: number
  total_pages: number
  next_cursor?: string | null
  has_more: boolean
}

export interface PageEnvelope<T> {
  data: T[]
  pagination: PaginationMeta
}

export interface ApiErrorEnvelope {
  code: string
  message: string
  details?: unknown
  request_id?: string
  retryable?: boolean
}

export interface ApiEnvelope<T> {
  data: T
  request_id?: string
}

export interface ApiRequestOptions extends Omit<RequestInit, 'body'> {
  body?: BodyInit | Record<string, unknown> | unknown[] | null
  timeoutMs?: number
  idempotencyKey?: string
  etag?: boolean
}

export interface ApiClientOptions {
  baseUrl?: string
  timeoutMs?: number
  fetcher?: typeof fetch
  defaultHeaders?: HeadersInit
  credentials?: RequestCredentials
}
