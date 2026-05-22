<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiJsonBlock,
  UiLoadingState,
  UiPageHeader,
  UiPageShell,
  UiPanel,
} from '@/components/ui'
import { apiFetch, ApiError } from '@/lib/client'
import type { components } from '@/api'

type HealthResponse = components['schemas']['HealthResponse']

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string; status?: number }
  | { kind: 'loaded'; data: HealthResponse }

const state = ref<LoadState>({ kind: 'loading' })

async function load(): Promise<void> {
  state.value = { kind: 'loading' }
  try {
    const data = await apiFetch<HealthResponse>('/api/v1/health')
    state.value = { kind: 'loaded', data }
  } catch (err) {
    if (err instanceof ApiError) {
      state.value = { kind: 'error', message: err.message, status: err.status }
    } else if (err instanceof Error) {
      state.value = { kind: 'error', message: err.message }
    } else {
      state.value = { kind: 'error', message: 'Unknown error' }
    }
  }
}

onMounted(load)

const prettyJson = computed<string>(() => {
  if (state.value.kind !== 'loaded') return ''
  return JSON.stringify(state.value.data, null, 2)
})

const dbStatus = computed<string>(() => {
  if (state.value.kind !== 'loaded') return 'unknown'
  return String(state.value.data.db_status ?? 'unknown')
})

const dbBadgeClass = computed<'success' | 'neutral' | 'danger'>(() => {
  const s = dbStatus.value.toLowerCase()
  if (s === 'ok' || s === 'ready' || s === 'healthy') {
    return 'success'
  }
  if (s === 'unknown') {
    return 'neutral'
  }
  return 'danger'
})

const schedulerRunning = computed<boolean | null>(() => {
  if (state.value.kind !== 'loaded') return null
  const v = state.value.data.scheduler_running
  return typeof v === 'boolean' ? v : null
})

const schedulerBadgeClass = computed<'success' | 'warning' | 'neutral'>(() => {
  const v = schedulerRunning.value
  if (v === true)
    return 'success'
  if (v === false) return 'warning'
  return 'neutral'
})

const schedulerLabel = computed<string>(() => {
  const v = schedulerRunning.value
  if (v === true) return 'running'
  if (v === false) return 'stopped'
  return 'unknown'
})
</script>

<template>
  <UiPageShell>
    <UiPageHeader
      title="StackOS"
      description="Agent-driven projects, plugins, run plans, resources, actions, and audit trails."
    />

    <UiPanel
      aria-live="polite"
      :aria-busy="state.kind === 'loading'"
      class="p-5"
    >
      <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 class="t-h2 text-fg-strong">
          Daemon health
        </h2>
        <UiButton
          variant="secondary"
          size="sm"
          :disabled="state.kind === 'loading'"
          @click="load"
        >
          Refresh
        </UiButton>
      </div>

      <UiLoadingState
        v-if="state.kind === 'loading'"
        label="Pinging /api/v1/health…"
      />

      <UiCallout
        v-else-if="state.kind === 'error'"
        tone="danger"
        class="mt-4"
        role="alert"
      >
        <div class="font-medium">
          Could not reach the daemon.
        </div>
        <div class="mt-1">
          <span v-if="state.status">HTTP {{ state.status }} &mdash; </span>{{ state.message }}
        </div>
        <div class="mt-2 text-xs opacity-80">
          Is the daemon running on <code>127.0.0.1:5180</code>? In dev,
          <code>pnpm dev</code> proxies <code>/api</code> to that origin.
        </div>
      </UiCallout>

      <div
        v-else
        class="mt-4 space-y-4"
      >
        <div class="flex flex-wrap gap-2">
          <UiBadge :tone="dbBadgeClass">
            db: {{ dbStatus }}
          </UiBadge>
          <UiBadge :tone="schedulerBadgeClass">
            scheduler: {{ schedulerLabel }}
          </UiBadge>
          <UiBadge
            v-if="state.data.version"
            tone="neutral"
          >
            v{{ state.data.version }}
          </UiBadge>
          <UiBadge
            v-if="typeof state.data.daemon_uptime_s === 'number'"
            tone="neutral"
          >
            uptime: {{ Math.round(state.data.daemon_uptime_s) }}s
          </UiBadge>
        </div>

        <UiJsonBlock
          :data="prettyJson"
          max-height="20rem"
          density="compact"
          aria-label="Daemon health JSON"
        />
      </div>
    </UiPanel>

    <footer class="text-xs text-fg-muted">
      Use Projects to pick a workspace and continue operating StackOS.
    </footer>
  </UiPageShell>
</template>
