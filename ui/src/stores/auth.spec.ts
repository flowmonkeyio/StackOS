// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { registerAuthStoreWithClient, useAuthStore } from './auth'
import { apiFetch, clearAuthStore } from '@/lib/client'

const ORIG_FETCH = globalThis.fetch

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })
  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    clearAuthStore()
    vi.restoreAllMocks()
  })

  it('starts in idle state', () => {
    const auth = useAuthStore()
    expect(auth.state.kind).toBe('idle')
    expect(auth.token).toBeNull()
    expect(auth.ready).toBe(false)
  })

  it('fetches the token via /api/v1/auth/ui-token and flips to ready', async () => {
    globalThis.fetch = vi.fn(async (path) => {
      if (path === '/api/v1/auth/session') return json({}, 404)
      return new Response(JSON.stringify({ token: 'abc' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const auth = useAuthStore()
    await auth.bootstrap()
    expect(auth.token).toBe('abc')
    expect(auth.state.kind).toBe('ready')
    expect(auth.ready).toBe(true)
  })

  it('uses online cookies and CSRF without ever bootstrapping a console bearer', async () => {
    const session = { status: 'authenticated', user_id: 'u1', environment_id: 'A', csrf_token: 'csrf' }
    globalThis.fetch = vi.fn(async (path) => path === '/api/v1/auth/ui-token'
      ? json({ detail: { code: 'sign_in_required' } }, 401) : json(session)) as typeof fetch
    const auth = useAuthStore()
    registerAuthStoreWithClient(auth)
    await auth.bootstrap()
    expect(auth.ready).toBe(true)
    expect(auth.mode).toBe('server')
    expect(auth.token).toBeNull()
    await apiFetch('/api/v1/projects', { method: 'POST', body: '{}' })
    const calls = vi.mocked(fetch).mock.calls
    expect(calls.map(([path]) => path)).toEqual(['/api/v1/auth/ui-token', '/api/v1/auth/session', '/api/v1/projects'])
    const init = calls[2]![1]!
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf')
    expect(new Headers(init.headers).has('Authorization')).toBe(false)
    expect(init.credentials).toBe('same-origin')
    expect(init.redirect).toBe('error')
  })

  it.each([401, 403, 503])('never falls back from an online rejection (%s)', async (status) => {
    globalThis.fetch = vi.fn(async () => json({ detail: { code: 'auth_unavailable' } }, status)) as typeof fetch
    const auth = useAuthStore()
    auth.mode = 'server'
    await auth.bootstrap()
    expect(auth.ready).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(auth.state.kind).toBe(status === 401 ? 'signed_out' : 'error')
  })

  it('does not interpret an unrelated 401 as online mode or retry another bootstrap', async () => {
    globalThis.fetch = vi.fn(async () => json({ detail: 'unauthorized' }, 401)) as typeof fetch
    const auth = useAuthStore()
    await auth.bootstrap()
    expect(auth.state.kind).toBe('error')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('leaves the private shell when logout discovers an already expired session', async () => {
    globalThis.fetch = vi.fn(async () => json({}, 401)) as typeof fetch
    const auth = useAuthStore(); auth.mode = 'server'; auth.state = {kind:'ready'}
    await auth.logout()
    expect(auth.state.kind).toBe('signed_out')
  })

  it('keeps successful login gated until a fresh document bootstraps its session', async () => {
    const auth = useAuthStore()
    auth.mode = 'server'; auth.state = {kind:'signed_out'}
    const session = {status:'authenticated',user_id:'u2',environment_id:'A',csrf_token:'u2-csrf'}
    globalThis.fetch = vi.fn(async () => json(session)) as typeof fetch
    await auth.submit('login',{email:'u2@example.test',password:'synthetic-password'})
    expect(auth.ready).toBe(false)
    expect(auth.userId).toBeNull()
    expect(auth.csrfToken).toBeNull()
    // The next document has new Pinia stores; only its session bootstrap may
    // admit private content after the browser has replaced the old document.
    const fresh = useAuthStore(createPinia())
    fresh.mode = 'server'
    await fresh.bootstrap()
    expect(fresh.ready).toBe(true)
    expect(fresh.userId).toBe('u2')
  })

  it('keeps recovery outside the private shell and invalidates access on a later 401', async () => {
    globalThis.fetch = vi.fn(async (path) => path === '/api/v1/auth/ui-token'
      ? json({ detail: { code: 'sign_in_required' } }, 401)
      : json({ status: 'recovery_required', user_id: 'u1', environment_id: 'A', csrf_token: 'csrf' })) as typeof fetch
    const auth = useAuthStore()
    registerAuthStoreWithClient(auth)
    await auth.bootstrap()
    expect(auth.state.kind).toBe('recovery')
    expect(auth.ready).toBe(false)
    await auth.submit('login', { email: 'u1@example.test', password: 'synthetic' })
    globalThis.fetch = vi.fn(async () => json({}, 401)) as typeof fetch
    await expect(apiFetch('/api/v1/projects')).rejects.toThrow()
    expect(auth.state.kind).toBe('signed_out')
    expect(auth.userId).toBeNull()
    expect(auth.csrfToken).toBeNull()
  })

  it('records an error state when the bootstrap call fails', async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify({ detail: 'nope' }), {
        status: 500,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const auth = useAuthStore()
    await auth.bootstrap()
    expect(auth.token).toBeNull()
    expect(auth.state.kind).toBe('error')
    if (auth.state.kind === 'error') {
      expect(auth.state.status).toBe(500)
    }
    expect(auth.ready).toBe(false)
  })
})

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}
