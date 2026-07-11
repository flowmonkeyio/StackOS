/**
 * Readiness derivation — "Can connected agents use this project safely?"
 *
 * Replaces the 16-row technical checklist with a small set of plain-language
 * checks and a single headline verdict (+ the one thing blocking, if any).
 * Web-surfaceable signals only: daemon health, background automation, provider
 * connections, and available actions. Richer system health (token/seed/migration
 * health, provider readiness) is desktop-only via lib/desktop.ts.
 *
 * Each source is isolated so a single failure becomes an "unknown" check rather
 * than a blank screen — no silent swallowing into a false "todo".
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type { SchemaAuthStatusOut, SchemaHealthResponse } from '@/api'
import type { Tone } from '@/design/status'
import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'

export type ReadinessState = 'ready' | 'attention' | 'blocked' | 'unknown'

export interface ReadinessCheck {
  key: string
  label: string
  state: ReadinessState
  /** Plain-language status line. */
  hint: string
  /** Whether this check gates the overall "ready to run" verdict. */
  critical: boolean
  /** In-app route to resolve, when applicable. */
  to: string | null
}

interface ReadinessRefreshOptions {
  authStatus?: Promise<SchemaAuthStatusOut>
  includeActions?: boolean
  refreshActions?: boolean
}

const STATE_TONE: Record<ReadinessState, Tone> = {
  ready: 'success',
  attention: 'warning',
  blocked: 'danger',
  unknown: 'neutral',
}

const STATE_RANK: Record<ReadinessState, number> = {
  blocked: 0,
  attention: 1,
  unknown: 2,
  ready: 3,
}

export function readinessTone(state: ReadinessState): Tone {
  return STATE_TONE[state]
}

export const useReadinessStore = defineStore('readiness', () => {
  const checks = ref<ReadinessCheck[]>([])
  const loading = ref(false)
  const version = ref<string | null>(null)
  const uptimeSeconds = ref<number | null>(null)
  const projectId = ref<number | null>(null)
  const cachedActionsCheck = ref<ReadinessCheck | null>(null)
  const actionsCheckedAt = ref(0)

  const ACTIONS_CACHE_MS = 5 * 60_000

  const score = computed(() => {
    if (checks.value.length === 0) return 0
    const ready = checks.value.filter((c) => c.state === 'ready').length
    return Math.round((ready / checks.value.length) * 100)
  })

  /** The single most important unresolved check (worst critical first). */
  const blocker = computed<ReadinessCheck | null>(() => {
    const unresolved = checks.value
      .filter((c) => c.state !== 'ready')
      .sort((a, b) => {
        if (a.critical !== b.critical) return a.critical ? -1 : 1
        return STATE_RANK[a.state] - STATE_RANK[b.state]
      })
    return unresolved[0] ?? null
  })

  const ready = computed(() => checks.value.every((c) => !c.critical || c.state === 'ready'))

  const headline = computed(() => {
    if (loading.value && checks.value.length === 0) return 'Checking readiness…'
    if (ready.value) return 'Ready for connected agents'
    const b = blocker.value
    if (!b) return 'Almost ready'
    return b.state === 'blocked' ? 'Not ready for agents' : 'Available, with setup left'
  })

  async function loadHealthChecks(base: string): Promise<ReadinessCheck[]> {
    try {
      const health = await apiFetch<SchemaHealthResponse>('/api/v1/health')
      version.value = (health.version as string) ?? null
      uptimeSeconds.value =
        typeof health.daemon_uptime_s === 'number' ? health.daemon_uptime_s : null
      const dbOk = health.db_status === 'ok'
      return [
        {
          key: 'daemon',
          label: 'Local service',
          state: dbOk ? 'ready' : 'blocked',
          hint: dbOk
            ? 'Running and connected to local storage.'
            : 'Local storage is unreachable.',
          critical: true,
          to: null,
        },
        {
          key: 'automation',
          label: 'Background automation',
          state: health.scheduler_running ? 'ready' : 'attention',
          hint: health.scheduler_running
            ? 'Scheduled and triggered work can run.'
            : 'Scheduler is stopped — scheduled work will not fire.',
          critical: false,
          to: `${base}/schedules`,
        },
      ]
    } catch {
      version.value = null
      uptimeSeconds.value = null
      return [
        {
          key: 'daemon',
          label: 'Local service',
          state: 'blocked',
          hint: 'Could not reach the local service.',
          critical: true,
          to: null,
        },
      ]
    }
  }

  async function loadConnectionsCheck(
    id: number,
    base: string,
    authStatus?: Promise<SchemaAuthStatusOut>,
  ): Promise<ReadinessCheck> {
    try {
      const auth = await (authStatus ??
        apiFetch<SchemaAuthStatusOut>(`/api/v1/projects/${id}/auth/status`))
      const providers = auth.providers ?? []
      const active = (auth.connections ?? []).filter((c) => c.revoked_at == null)
      const connected = active.filter((c) => c.status === 'connected' || c.status === 'used')
      let state: ReadinessState = 'ready'
      let hint = 'No external services required.'
      if (providers.length > 0 && connected.length === 0) {
        state = 'attention'
        hint = 'No services connected yet — agents can’t reach external providers.'
      } else if (connected.length > 0) {
        const issues = active.length - connected.length
        state = issues > 0 ? 'attention' : 'ready'
        hint =
          issues > 0
            ? `${connected.length} connected, ${issues} need attention.`
            : `${connected.length} service${connected.length === 1 ? '' : 's'} connected.`
      }
      return {
        key: 'connections',
        label: 'Connections',
        state,
        hint,
        critical: false,
        to: `${base}/connections`,
      }
    } catch {
      return {
        key: 'connections',
        label: 'Connections',
        state: 'unknown',
        hint: 'Connection status is unavailable right now.',
        critical: false,
        to: `${base}/connections`,
      }
    }
  }

  async function loadActionsCheck(id: number, base: string): Promise<ReadinessCheck> {
    try {
      const integ = await callOperation<{
        count?: number | null
        connected_count?: number | null
        hidden_action_count?: number | null
      }>('integration.list', { project_id: id })
      const count = integ.count ?? 0
      const hidden = integ.hidden_action_count ?? 0
      let state: ReadinessState = 'ready'
      let hint = 'Agent actions are available.'
      if (count === 0) {
        state = 'attention'
        hint = 'No integrations enabled yet.'
      } else if (hidden > 0) {
        state = 'attention'
        hint = `${hidden} action${hidden === 1 ? '' : 's'} unlock once setup is finished.`
      }
      return {
        key: 'actions',
        label: 'Agent actions',
        state,
        hint,
        critical: false,
        to: `${base}/plugins`,
      }
    } catch {
      return {
        key: 'actions',
        label: 'Agent actions',
        state: 'unknown',
        hint: 'Action availability is unavailable right now.',
        critical: false,
        to: `${base}/plugins`,
      }
    }
  }

  async function refresh(id: number, options: ReadinessRefreshOptions = {}): Promise<void> {
    if (projectId.value !== id) {
      cachedActionsCheck.value = null
      actionsCheckedAt.value = 0
    }
    projectId.value = id
    loading.value = true
    const base = `/projects/${id}`

    const connectionsPromise = loadConnectionsCheck(id, base, options.authStatus)
    const includeActions = options.includeActions ?? true
    const actionsAreFresh =
      !options.refreshActions &&
      cachedActionsCheck.value !== null &&
      Date.now() - actionsCheckedAt.value < ACTIONS_CACHE_MS
    const actionsPromise = includeActions
      ? actionsAreFresh
        ? Promise.resolve(cachedActionsCheck.value!)
        : loadActionsCheck(id, base).then((check) => {
            cachedActionsCheck.value = check
            actionsCheckedAt.value = Date.now()
            return check
          })
      : null

    const healthChecks = await loadHealthChecks(base)
    checks.value = healthChecks
    const [connectionsCheck, actionsCheck] = await Promise.all([
      connectionsPromise,
      actionsPromise,
    ])
    checks.value = actionsCheck
      ? [...healthChecks, connectionsCheck, actionsCheck]
      : [...healthChecks, connectionsCheck]
    loading.value = false
  }

  function reset(): void {
    checks.value = []
    version.value = null
    uptimeSeconds.value = null
    projectId.value = null
    cachedActionsCheck.value = null
    actionsCheckedAt.value = 0
  }

  return {
    checks,
    loading,
    version,
    uptimeSeconds,
    projectId,
    score,
    blocker,
    ready,
    headline,
    refresh,
    reset,
  }
})
