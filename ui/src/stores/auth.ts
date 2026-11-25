// Auth store — bootstraps the daemon's bearer token at app start.
//
// The token lives at `~/.local/state/stackos/auth.token` (mode 0600);
// the browser can't read it directly, so the daemon exposes
// `GET /api/v1/auth/ui-token` (whitelisted, same-origin + Host-loopback
// enforced; see docs/security.md). On bootstrap we GET that endpoint, hold
// the token in a Pinia ref, and register the store with `lib/client.ts`
// so every subsequent `apiFetch` carries `Authorization: Bearer <token>`.
//
// The token is intentionally NOT persisted to localStorage / sessionStorage:
// the daemon rotates it on every `make install`, so we always re-fetch on
// page load. Holding it only in memory also limits the blast radius of an
// XSS bug — there is no DOM-readable copy outside the running JS context.

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { apiFetch, ApiError, setAuthStore } from '@/lib/client'

interface UiTokenResponse {
  token: string
}

export type AuthBootstrapState =
  | { kind: 'idle' }
  | { kind: 'bootstrapping' }
  | { kind: 'ready' }
  | { kind: 'error'; message: string; status?: number }

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(null)
  const state = ref<AuthBootstrapState>({ kind: 'idle' })

  const ready = computed<boolean>(() => state.value.kind === 'ready' && token.value !== null)

  async function bootstrap(): Promise<void> {
    if (state.value.kind === 'bootstrapping') return
    state.value = { kind: 'bootstrapping' }
    try {
      // The bootstrap endpoint is whitelisted from auth, so we don't pass
      // a token. We DO need a JSON `Accept` header (the wrapper sets it).
      const res = await apiFetch<UiTokenResponse>('/api/v1/auth/ui-token')
      if (!res || typeof res.token !== 'string' || res.token.length === 0) {
        throw new Error('bootstrap response missing token field')
      }
      token.value = res.token
      state.value = { kind: 'ready' }
    } catch (err) {
      token.value = null
      if (err instanceof ApiError) {
        state.value = { kind: 'error', message: err.message, status: err.status }
      } else if (err instanceof Error) {
        state.value = { kind: 'error', message: err.message }
      } else {
        state.value = { kind: 'error', message: 'unknown bootstrap error' }
      }
    }
  }

  function clear(): void {
    token.value = null
    state.value = { kind: 'idle' }
  }

  return { token, state, ready, bootstrap, clear }
})

/**
 * Wire the auth store into `lib/client.ts`. Called once from `main.ts`
 * after `app.use(pinia)` so the fetch wrapper can pull the token without
 * re-importing the Pinia store on every request.
 */
export function registerAuthStoreWithClient(store: ReturnType<typeof useAuthStore>): void {
  setAuthStore({
    get token(): string | null {
      return store.token
    },
  })
}
