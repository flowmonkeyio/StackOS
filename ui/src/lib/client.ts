// Same-origin StackOS transport. Online sessions use HttpOnly cookies and an
// in-memory CSRF value; the standalone console uses its restricted bearer.
//
// Standalone explicitly returns a console token. A server explicitly rejects
// that bootstrap, selecting its private session flow without a local fallback.
//
// A rejected private request invalidates the online shell. Never retry writes.
//
// 412 (If-Match precondition failed) is left for the caller.

import type { AuthStoreLike } from '@/stores/auth-store-shape'

export type ApiInit = RequestInit & { token?: string }

export interface ApiErrorEnvelope {
  detail: string
  code?: number
  retryable?: boolean
  data?: unknown
  retry_after?: number | null
  hint?: string | null
}

interface JsonObject {
  [key: string]: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown
  readonly detail: string
  readonly code?: number
  readonly retryable?: boolean
  readonly data?: unknown
  readonly retryAfter?: number | null
  readonly hint?: string | null
  readonly requestId?: string | null

  constructor(message: string, status: number, body: unknown, requestId?: string | null) {
    const envelope = parseApiErrorEnvelope(body)
    const detail = envelope?.detail ?? message
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.body = body
    this.detail = detail
    this.code = envelope?.code
    this.retryable = envelope?.retryable
    this.data = envelope?.data
    this.retryAfter = envelope?.retry_after
    this.hint = envelope?.hint
    this.requestId = requestId
  }
}

const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

// The auth store is wired in at app-bootstrap time via `setAuthStore`. We
// avoid importing the Pinia store directly here so the client module stays
// usable from places that haven't called `app.use(pinia)` yet (e.g. early
// bootstrap, pre-pinia tests). The runtime contract: `setAuthStore` must
// be called before any authenticated `apiFetch` lands.
let _authStore: AuthStoreLike | null = null

export function setAuthStore(store: AuthStoreLike): void {
  _authStore = store
}

export function clearAuthStore(): void {
  _authStore = null
}

function buildUrl(path: string): string {
  const base = API_BASE.replace(/\/$/, '')
  const suffix = path.startsWith('/') ? path : `/${path}`
  const target = /^https?:\/\//.test(path) ? path : `${base}${suffix}`
  const origin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin
  const url = new URL(target, origin)
  if (url.origin !== origin || url.username || url.password) {
    throw new Error('StackOS requests must use the selected app origin.')
  }
  return `${url.pathname}${url.search}`
}

function buildHeaders(init: ApiInit, defaultAccept: string): Headers {
  const { token: explicitToken, headers } = init
  const finalHeaders = new Headers(headers)
  if (!finalHeaders.has('Accept')) finalHeaders.set('Accept', defaultAccept)
  // Explicit token wins (used by tests + the bootstrap probe itself).
  // Otherwise pull from the auth store if it's been wired up.
  const token = explicitToken ?? _authStore?.token ?? null
  if (token) {
    finalHeaders.set('Authorization', `Bearer ${token}`)
  }
  if (_authStore?.mode === 'server' && !['GET', 'HEAD', 'OPTIONS'].includes((init.method ?? 'GET').toUpperCase())) {
    if (_authStore.csrfToken) finalHeaders.set('X-CSRF-Token', _authStore.csrfToken)
  }
  return finalHeaders
}

function isSessionEndpoint(path: string): boolean {
  return /^\/api\/v1\/auth\/(?:ui-token|session|register|login|resend|recover|verify|callback|refresh|logout|password)(?:\?|$)/.test(buildUrl(path))
}

export async function apiFetch<T = unknown>(path: string, init: ApiInit = {}, renewed = false): Promise<T> {
  const rest = requestInitWithoutAuthOverrides(init)
  const finalHeaders = buildHeaders(init, 'application/json')
  const res = await fetch(buildUrl(path), { ...rest, headers: finalHeaders, credentials: 'same-origin', redirect: 'error' })
  const contentType = res.headers.get('content-type') ?? ''
  const isJson = contentType.includes('application/json')
  const body = isJson ? await res.json().catch(() => null) : await res.text()

  if (!res.ok) {
    const error = new ApiError(
      `Request to ${path} failed with ${res.status}`,
      res.status,
      body,
      res.headers.get('x-request-id'),
    )
    if (res.status === 401 && !isSessionEndpoint(path)) {
      const refreshed = await _authStore?.onUnauthorized?.(error, renewed)
      // Admission rejected the first request. Only reads may be replayed;
      // callers explicitly retry writes after renewal.
      if (refreshed && ['GET', 'HEAD'].includes((init.method ?? 'GET').toUpperCase())) return apiFetch<T>(path, init, true)
    }
    throw error
  }
  return body as T
}

export async function apiStream(path: string, init: ApiInit = {}, renewed = false): Promise<Response> {
  const rest = requestInitWithoutAuthOverrides(init)
  const finalHeaders = buildHeaders(init, 'text/event-stream')
  const res = await fetch(buildUrl(path), { ...rest, headers: finalHeaders, credentials: 'same-origin', redirect: 'error' })
  if (!res.ok) {
    const contentType = res.headers.get('content-type') ?? ''
    const isJson = contentType.includes('application/json')
    const body = isJson ? await res.json().catch(() => null) : await res.text()
    const error = new ApiError(
      `Request to ${path} failed with ${res.status}`,
      res.status,
      body,
      res.headers.get('x-request-id'),
    )
    if (res.status === 401) {
      const refreshed = await _authStore?.onUnauthorized?.(error, renewed)
      if (refreshed && ['GET', 'HEAD'].includes((init.method ?? 'GET').toUpperCase())) return apiStream(path, init, true)
    }
    throw error
  }
  return res
}

function requestInitWithoutAuthOverrides(init: ApiInit): RequestInit {
  const rest: ApiInit = { ...init }
  delete rest.token
  delete rest.headers
  return rest
}

export function formatApiError(err: unknown, fallback = 'Request failed'): string {
  if (err instanceof ApiError) {
    const parts = [err.detail || err.message || fallback]
    const dataSummary = summarizeErrorData(err.data)
    if (dataSummary) parts.push(dataSummary)
    if (err.hint) parts.push(err.hint)
    if (err.retryable === true && err.retryAfter) {
      parts.push(`Retry after ${err.retryAfter}s.`)
    }
    if (err.requestId) parts.push(`Request id: ${err.requestId}.`)
    return parts.join(' ')
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}

function parseApiErrorEnvelope(body: unknown): ApiErrorEnvelope | null {
  if (isJsonObject(body) && isJsonObject(body.detail)) {
    const nested = envelopeFromObject(body.detail)
    if (nested) return nested
  }
  if (isJsonObject(body)) {
    const direct = envelopeFromObject(body)
    if (direct) return direct
  }
  if (typeof body === 'string' && body.trim().length > 0) {
    return { detail: body.trim() }
  }
  return null
}

function envelopeFromObject(raw: JsonObject): ApiErrorEnvelope | null {
  const detail = raw.detail
  if (typeof detail === 'string') {
    return {
      detail,
      code: typeof raw.code === 'number' ? raw.code : undefined,
      retryable: typeof raw.retryable === 'boolean' ? raw.retryable : undefined,
      data: 'data' in raw ? raw.data : undefined,
      retry_after: typeof raw.retry_after === 'number' || raw.retry_after === null
        ? raw.retry_after
        : undefined,
      hint: typeof raw.hint === 'string' || raw.hint === null ? raw.hint : undefined,
    }
  }
  if (Array.isArray(detail)) {
    return {
      detail: 'request validation failed',
      data: { errors: detail },
    }
  }
  return null
}

function summarizeErrorData(data: unknown): string | null {
  if (!isJsonObject(data)) return null

  const budget = summarizeBudgetData(data)
  if (budget) return budget

  const errors = data.errors
  if (Array.isArray(errors) && errors.length > 0) {
    const messages = errors
      .map((item) => {
        if (!isJsonObject(item)) return null
        const loc = Array.isArray(item.loc) ? item.loc.join('.') : null
        const msg = typeof item.msg === 'string' ? item.msg : null
        if (!msg) return null
        return loc ? `${loc}: ${msg}` : msg
      })
      .filter((item): item is string => item !== null)
      .slice(0, 2)
    if (messages.length > 0) return appendNextAction(messages.join(' '), data)
  }

  return appendNextAction(null, data)
}

function appendNextAction(summary: string | null, data: JsonObject): string | null {
  const nextAction = data.next_action
  if (typeof nextAction !== 'string' || nextAction.trim() === '') return summary
  return [summary, nextAction.trim()].filter((part): part is string => Boolean(part)).join(' ')
}

function summarizeBudgetData(data: JsonObject): string | null {
  const cap = data.monthly_budget_usd
  const current = data.current_month_spend
  const attempted = data.attempted_cost_usd
  if (typeof cap !== 'number' || typeof current !== 'number') return null

  const attemptedPart = typeof attempted === 'number' ? ` + ${formatUsd(attempted)} attempted` : ''
  return `Budget: ${formatUsd(current)} spent${attemptedPart} of ${formatUsd(cap)} cap.`
}

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
