// Online sessions are server-owned; standalone keeps its in-memory console token.
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { apiFetch, ApiError, setAuthStore } from '@/lib/client'

interface UserSession {
  status: 'authenticated' | 'recovery_required'
  user_id: string
  environment_id: string
  csrf_token: string
}

export type AuthBootstrapState =
  | { kind: 'idle' | 'bootstrapping' | 'renewing' | 'ready' | 'signed_out' | 'recovery' }
  | { kind: 'error'; message: string; status?: number }

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(null)
  const csrfToken = ref<string | null>(null)
  const mode = ref<'standalone' | 'server'>('standalone')
  const userId = ref<string | null>(null)
  const environmentId = ref<string | null>(null)
  const state = ref<AuthBootstrapState>({ kind: 'idle' })
  const ready = computed(() => state.value.kind === 'ready')
  let bootstrapping: Promise<void> | null = null
  let renewing: Promise<boolean> | null = null
  let generation = 0

  async function refreshSession(error?: unknown, renewed = false): Promise<boolean> {
    if (renewing) return renewing
    const code = authErrorCode(error)
    if (renewed || mode.value !== 'server' || !csrfToken.value ||
      ['session_expired_or_invalid', 'session_invalid', 'session_invalid_sign_in_required'].includes(code)) {
      expire()
      return false
    }
    const previous = state.value.kind === 'recovery' ? 'recovery' : 'ready'
    const started = generation
    state.value = { kind: 'renewing' }
    renewing = (async () => {
      try {
        const result = await apiFetch<{status: string; csrf_token: string}>('/api/v1/auth/refresh', { method: 'POST' })
        if (started !== generation) return false
        if (result.status !== 'authenticated' || result.csrf_token !== csrfToken.value) throw new Error('Invalid session renewal response.')
        state.value = { kind: previous }
        return true
      } catch (err) {
        if (started !== generation) return false
        if (err instanceof ApiError && [401, 403].includes(err.status)) expire()
        else state.value = { kind: 'error', message: 'StackOS is temporarily unavailable. Try again to reconnect.' }
        return false
      }
    })()
    try { return await renewing } finally { renewing = null }
  }

  function clear(): void {
    generation++
    token.value = csrfToken.value = userId.value = environmentId.value = null
    state.value = { kind: 'idle' }
  }

  function expire(): void {
    clear()
    state.value = mode.value === 'server'
      ? { kind: 'signed_out' }
      : { kind: 'error', message: 'Please reconnect to the daemon.' }
  }

  function acceptSession(session: UserSession): void {
    if (!session.user_id || !session.environment_id || !session.csrf_token ||
      !['authenticated', 'recovery_required'].includes(session.status)) {
      throw new Error('Invalid StackOS session response.')
    }
    mode.value = 'server'
    token.value = null
    csrfToken.value = session.csrf_token
    userId.value = session.user_id
    environmentId.value = session.environment_id
    state.value = { kind: session.status === 'recovery_required' ? 'recovery' : 'ready' }
  }

  async function bootstrap(): Promise<void> {
    if (bootstrapping) return bootstrapping
    bootstrapping = (async () => {
      clear()
      state.value = { kind: 'bootstrapping' }
      try {
        if (mode.value !== 'server') {
          try {
            const res = await apiFetch<{ token: string }>('/api/v1/auth/ui-token', { cache: 'no-store' })
            if (!res?.token) throw new Error('Bootstrap response missing token field.')
            token.value = res.token
            state.value = { kind: 'ready' }
            return
          } catch (err) {
            // The server explicitly rejects console bootstrap. A network failure
            // or any other response cannot choose another authentication mode.
            if (!(err instanceof ApiError) || err.status !== 401 ||
              (err.body as { detail?: { code?: string } })?.detail?.code !== 'sign_in_required') throw err
            mode.value = 'server'
          }
        }
        try {
          acceptSession(await apiFetch<UserSession>('/api/v1/auth/session', { cache: 'no-store' }))
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) {
            state.value = { kind: 'signed_out' }
            return
          }
          throw err
        }
      } catch (err) {
        state.value = { kind: 'error', message: err instanceof Error ? err.message : 'Authentication unavailable.',
          ...(err instanceof ApiError ? { status: err.status } : {}) }
      }
    })()
    try { await bootstrapping } finally { bootstrapping = null }
  }

  async function submit(action: 'register' | 'login' | 'resend' | 'recover' | 'password' | 'verify', body: Record<string, string>): Promise<unknown> {
    // A successful login sets the server cookie, but this document can still
    // hold another user's project caches. Only the fresh document's bootstrap
    // may make the private shell ready, including while navigation is delayed.
    if (action === 'login') expire()
    const result = await apiFetch<UserSession>(`/api/v1/auth/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (action === 'verify') {
      // Confirmation can replace the browser's previous account cookie. Never
      // reopen that document's private caches. Recovery only admits its form.
      expire()
      if (body.type === 'recovery') acceptSession(result)
    }
    if (action === 'password') expire()
    return result
  }

  async function logout(): Promise<void> {
    try {
      await apiFetch('/api/v1/auth/logout', { method: 'POST' })
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 401) throw err
    }
    expire()
  }

  return { token, csrfToken, mode, userId, environmentId, state, ready, bootstrap, clear, expire, submit, logout, refreshSession }
})

export function registerAuthStoreWithClient(store: ReturnType<typeof useAuthStore>): void {
  setAuthStore({
    get token() { return store.token },
    get csrfToken() { return store.csrfToken },
    get mode() { return store.mode },
    onUnauthorized: (error, renewed) => store.refreshSession(error, renewed),
  })
}

export function authErrorCode(err: unknown): string {
  return err instanceof ApiError ? (err.body as { detail?: { code?: string } })?.detail?.code ?? '' : ''
}

export function authDestination(candidate: unknown): string {
  if (typeof candidate !== 'string' || !candidate.startsWith('/') || candidate.includes('\\')) return '/'
  const url = new URL(candidate, window.location.origin)
  if (url.origin !== window.location.origin || url.username || url.password) return '/'
  return url.pathname === '/' || /^\/projects\/[1-9]\d*(?:\/[a-zA-Z0-9_-]+)*\/?$/.test(url.pathname)
    ? `${url.pathname}${url.search}${url.hash}` : '/'
}
