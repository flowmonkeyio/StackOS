<script setup lang="ts">
// SetupStatusTab — the calm "Is StackOS ready?" surface.
//
// Replaces the 16-row technical checklist with a plain-language verdict (the
// readiness store), the single most important blocking item, the few checks
// that matter, and direct links to where setup actually happens. Granular
// registry detail (operation contracts, presets, skills) lives in the demoted
// Developer surfaces, not here.

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { UiButton, UiCard, UiIcon, UiScoreMeter, UiSkeleton } from '@/components/ui'
import { formatDurationMinutes } from '@/lib/stackos/time'
import { readinessTone, useReadinessStore, type ReadinessState } from '@/stores/readiness'

const route = useRoute()
const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const base = computed(() => `/projects/${projectId.value}`)

const readiness = useReadinessStore()
const loaded = computed(() => readiness.checks.length > 0)

const busy = ref(false)
async function refresh(): Promise<void> {
  const id = projectId.value
  if (!id || Number.isNaN(id)) return
  busy.value = true
  try {
    await readiness.refresh(id)
  } finally {
    busy.value = false
  }
}

onMounted(refresh)

const meterTone = computed<'success' | 'warning' | 'danger'>(() => {
  if (readiness.ready) return 'success'
  return readiness.blocker?.state === 'blocked' ? 'danger' : 'warning'
})

const CHECK_ICON: Record<ReadinessState, string> = {
  ready: 'check-circle',
  attention: 'alert-triangle',
  blocked: 'x-circle',
  unknown: 'circle',
}
const TONE_TEXT = {
  success: 'text-success-fg',
  warning: 'text-warning-fg',
  danger: 'text-danger-fg',
  info: 'text-info-fg',
  neutral: 'text-fg-subtle',
} as const

function checkIconClass(state: ReadinessState): string {
  return TONE_TEXT[readinessTone(state)]
}

const uptimeLabel = computed(() =>
  readiness.uptimeSeconds != null ? formatDurationMinutes(readiness.uptimeSeconds / 60) : null,
)

const setupLinks = computed(() => [
  {
    key: 'connections',
    label: 'Connections',
    description: 'Connect the services agents use. Secrets stay on this machine.',
    icon: 'link',
    to: `${base.value}/connections`,
  },
  {
    key: 'automation',
    label: 'Automation',
    description: 'Schedule recurring agent work and maintenance.',
    icon: 'calendar',
    to: `${base.value}/schedules`,
  },
  {
    key: 'spend',
    label: 'Spend',
    description: 'Set monthly budgets and watch provider usage.',
    icon: 'banknotes',
    to: `${base.value}/cost-budget`,
  },
  {
    key: 'plugins',
    label: 'Plugins',
    description: 'Enable the capabilities and actions for this project.',
    icon: 'puzzle',
    to: `${base.value}/plugins`,
  },
])
</script>

<template>
  <div class="space-y-5">
    <!-- Readiness verdict -->
    <UiCard
      section
      aria-label="Readiness"
    >
      <template #header>
        <h2 class="t-h3 text-fg-strong">
          Readiness
        </h2>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="busy"
          @click="refresh"
        >
          Recheck
        </UiButton>
      </template>

      <div
        v-if="!loaded"
        class="flex items-center gap-5"
      >
        <UiSkeleton
          shape="circle"
          width="4.5rem"
          height="4.5rem"
        />
        <div class="flex-1 space-y-2">
          <UiSkeleton
            shape="line"
            width="14rem"
          />
          <UiSkeleton
            shape="line"
            width="20rem"
          />
        </div>
      </div>

      <div
        v-else
        class="flex flex-col gap-5 sm:flex-row sm:items-center"
      >
        <UiScoreMeter
          :value="readiness.score"
          :tone="meterTone"
          variant="circular"
          size="lg"
        />
        <div class="min-w-0 flex-1">
          <p class="t-h2 text-fg-strong">
            {{ readiness.headline }}
          </p>
          <p class="mt-1 text-sm text-fg-muted">
            <template v-if="readiness.ready">
              Agents can connect, run, and act for this project.
            </template>
            <template v-else-if="readiness.blocker">
              {{ readiness.blocker.hint }}
            </template>
          </p>
          <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-subtle">
            <span v-if="readiness.version">StackOS v{{ readiness.version }}</span>
            <template v-if="uptimeLabel">
              <span aria-hidden="true">·</span>
              <span>up {{ uptimeLabel }}</span>
            </template>
          </div>
        </div>
        <div
          v-if="!readiness.ready && readiness.blocker?.to"
          class="shrink-0"
        >
          <UiButton
            variant="primary"
            size="sm"
            @click="$router.push(readiness.blocker.to)"
          >
            {{ readiness.blocker.label }}
          </UiButton>
        </div>
      </div>
    </UiCard>

    <!-- The handful of checks that matter -->
    <UiCard
      v-if="loaded"
      section
      :padded="false"
      class="overflow-hidden"
      aria-label="Readiness checks"
    >
      <template #header>
        <h2 class="t-h3 text-fg-strong">
          Checks
        </h2>
      </template>
      <ul class="divide-y divide-border-subtle">
        <li
          v-for="check in readiness.checks"
          :key="check.key"
          class="flex items-center gap-3 px-4 py-3"
        >
          <UiIcon
            :name="CHECK_ICON[check.state]"
            :class="['h-4 w-4 shrink-0', checkIconClass(check.state)]"
            aria-hidden="true"
          />
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-fg-strong">
              {{ check.label }}
            </p>
            <p class="mt-0.5 text-2xs text-fg-subtle">
              {{ check.hint }}
            </p>
          </div>
          <UiButton
            v-if="check.to && check.state !== 'ready'"
            variant="secondary"
            size="sm"
            @click="$router.push(check.to)"
          >
            Fix
          </UiButton>
        </li>
      </ul>
    </UiCard>

    <!-- Where setup happens -->
    <UiCard
      section
      :padded="false"
      class="overflow-hidden"
      aria-label="Continue setup"
    >
      <template #header>
        <h2 class="t-h3 text-fg-strong">
          Continue setup
        </h2>
      </template>
      <ul class="grid gap-px bg-border-subtle sm:grid-cols-2">
        <li
          v-for="link in setupLinks"
          :key="link.key"
        >
          <RouterLink
            :to="link.to"
            class="focus-ring-inset group flex h-full items-start gap-3 bg-bg-surface px-4 py-4 transition-colors duration-fast hover:bg-bg-surface-alt"
          >
            <span
              class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-bg-surface-alt text-fg-muted group-hover:bg-bg-surface"
              aria-hidden="true"
            >
              <UiIcon
                :name="link.icon"
                class="h-4 w-4"
              />
            </span>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5">
                <span class="text-sm font-medium text-fg-strong">{{ link.label }}</span>
                <UiIcon
                  name="arrow-right"
                  class="h-3.5 w-3.5 text-fg-subtle opacity-0 transition-opacity duration-fast group-hover:opacity-100"
                  aria-hidden="true"
                />
              </div>
              <p class="mt-0.5 text-2xs text-fg-muted">
                {{ link.description }}
              </p>
            </div>
          </RouterLink>
        </li>
      </ul>
    </UiCard>
  </div>
</template>
