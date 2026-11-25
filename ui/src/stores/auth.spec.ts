// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useAuthStore } from './auth'

const ORIG_FETCH = globalThis.fetch

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })
  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('starts in idle state', () => {
    const auth = useAuthStore()
    expect(auth.state.kind).toBe('idle')
    expect(auth.token).toBeNull()
    expect(auth.ready).toBe(false)
  })

  it('fetches the token via /api/v1/auth/ui-token and flips to ready', async () => {
    globalThis.fetch = vi.fn(async () => {
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
