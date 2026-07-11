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

import { UiBadge, UiButton, UiCard, UiIcon, UiMedallion, UiSkeleton } from '@/components/ui'
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

const requiredChecks = computed(() => readiness.checks.filter((check) => check.critical))
const optionalChecks = computed(() => readiness.checks.filter((check) => !check.critical))
const unresolvedRequired = computed(() =>
  requiredChecks.value.filter((check) => check.state !== 'ready'),
)
const unresolvedOptional = computed(() =>
  optionalChecks.value.filter((check) => check.state !== 'ready'),
)
const nextCheck = computed(() => unresolvedRequired.value[0] ?? unresolvedOptional.value[0] ?? null)

const readinessState = computed(() => {
  if (unresolvedRequired.value.length > 0) {
    return {
      tone: 'danger' as const,
      label: 'Needs setup',
      title: 'Connected agents cannot use this project safely yet',
      detail: nextCheck.value?.hint ?? 'Repair the local runtime before continuing.',
    }
  }
  if (unresolvedOptional.value.length > 0) {
    return {
      tone: 'warning' as const,
      label: 'Core ready',
      title: 'Ready for core use, with optional setup remaining',
      detail: 'Connected agents can use local project capabilities. Finish only the services and guardrails relevant to the work you plan to run.',
    }
  }
  return {
    tone: 'success' as const,
    label: 'Ready',
    title: 'This project is ready for connected agents',
    detail: 'The local runtime is healthy and the currently configured capabilities are available.',
  }
})

function checkActionLabel(key: string): string {
  if (key === 'connections') return 'Review services'
  if (key === 'automation') return 'Open automation'
  if (key === 'actions') return 'Review capabilities'
  return 'Open setup'
}

const setupLinks = computed(() => [
  {
    key: 'connections',
    label: 'Connections',
    description: 'Connect the services agents use. Secrets stay on this machine.',
    icon: 'link',
    to: `${base.value}/connections?section=services`,
  },
  {
    key: 'automation',
    label: 'Automation',
    description: 'Schedule recurring agent work and maintenance.',
    icon: 'calendar',
    to: `${base.value}/schedules?from=setup`,
  },
  {
    key: 'spend',
    label: 'Spend',
    description: 'Set monthly budgets and watch provider usage.',
    icon: 'banknotes',
    to: `${base.value}/cost-budget?from=setup`,
  },
  {
    key: 'plugins',
    label: 'Plugins',
    description: 'Enable the capabilities and actions for this project.',
    icon: 'puzzle',
    to: `${base.value}/plugins?from=setup`,
  },
])
</script>

<template>
  <div class="space-y-5">
    <UiSkeleton
      v-if="!loaded"
      shape="block"
      height="11rem"
    />

    <section
      v-else
      class="overflow-hidden rounded-xl border border-strong bg-bg-surface shadow-sm"
      aria-labelledby="setup-state-title"
    >
      <div class="grid gap-5 p-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(260px,0.65fr)] lg:p-6">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <UiBadge :tone="readinessState.tone">
              {{ readinessState.label }}
            </UiBadge>
            <span class="text-2xs text-fg-subtle">Project readiness</span>
          </div>
          <h2
            id="setup-state-title"
            class="mt-3 max-w-3xl text-2xl font-semibold tracking-tight text-fg-strong"
          >
            {{ readinessState.title }}
          </h2>
          <p class="mt-2 max-w-2xl text-sm leading-6 text-fg-muted">
            {{ readinessState.detail }}
          </p>
          <div class="mt-5 flex flex-wrap gap-2">
            <UiButton
              v-if="nextCheck?.to"
              @click="$router.push(nextCheck.to)"
            >
              {{ checkActionLabel(nextCheck.key) }}
            </UiButton>
            <UiButton
              variant="secondary"
              icon-left="refresh"
              :loading="busy"
              @click="refresh"
            >
              Recheck now
            </UiButton>
          </div>
        </div>

        <div class="rounded-lg border border-subtle bg-bg-surface-alt p-4">
          <p class="t-overline text-fg-subtle">
            What this verdict means
          </p>
          <dl class="mt-3 space-y-3">
            <div class="flex items-center justify-between gap-3">
              <dt class="text-xs text-fg-muted">
                Required checks
              </dt>
              <dd class="text-sm font-semibold text-fg-strong">
                {{ requiredChecks.length - unresolvedRequired.length }}/{{ requiredChecks.length }} ready
              </dd>
            </div>
            <div class="flex items-center justify-between gap-3">
              <dt class="text-xs text-fg-muted">
                Optional setup items
              </dt>
              <dd class="text-sm font-semibold text-fg-strong">
                {{ unresolvedOptional.length }} remaining
              </dd>
            </div>
            <div class="flex items-center justify-between gap-3">
              <dt class="text-xs text-fg-muted">
                Local runtime
              </dt>
              <dd class="text-sm font-semibold text-fg-strong">
                {{ uptimeLabel ? `Up ${uptimeLabel}` : 'Unavailable' }}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </section>

    <UiCard
      v-if="loaded"
      section
      :padded="false"
      class="overflow-hidden"
      aria-label="Setup path"
    >
      <template #header>
        <div>
          <h2 class="t-h3 text-fg-strong">
            Setup path
          </h2>
          <p class="mt-0.5 text-2xs text-fg-subtle">
            Required runtime state first; optional capability setup second.
          </p>
        </div>
      </template>
      <ol class="divide-y divide-border-subtle">
        <li
          v-for="(check, index) in readiness.checks"
          :key="check.key"
          class="grid gap-3 px-4 py-4 sm:grid-cols-[2rem_minmax(0,1fr)_auto] sm:items-center"
        >
          <span
            class="inline-flex h-7 w-7 items-center justify-center rounded-full border text-xs font-semibold"
            :class="check.state === 'ready' ? 'border-success-border bg-success-subtle text-success-fg' : 'border-warning-border bg-warning-subtle text-warning-fg'"
          >
            <UiIcon
              v-if="check.state === 'ready'"
              name="check"
              class="h-3.5 w-3.5"
              aria-hidden="true"
            />
            <template v-else>{{ index + 1 }}</template>
          </span>
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <p class="text-sm font-semibold text-fg-strong">
                {{ check.label }}
              </p>
              <UiBadge
                :tone="check.critical ? 'accent' : 'neutral'"
                size="sm"
              >
                {{ check.critical ? 'Required' : 'Optional' }}
              </UiBadge>
            </div>
            <p class="mt-1 text-xs leading-5 text-fg-muted">
              {{ check.hint }}
            </p>
          </div>
          <div class="flex items-center gap-2 sm:justify-end">
            <UiIcon
              :name="CHECK_ICON[check.state]"
              :class="['h-4 w-4 shrink-0', checkIconClass(check.state)]"
              aria-hidden="true"
            />
            <UiButton
              v-if="check.to && check.state !== 'ready'"
              variant="secondary"
              size="sm"
              @click="$router.push(check.to)"
            >
              {{ checkActionLabel(check.key) }}
            </UiButton>
          </div>
        </li>
      </ol>
    </UiCard>

    <section aria-labelledby="optional-setup-title">
      <div class="mb-3">
        <h2
          id="optional-setup-title"
          class="t-h3 text-fg-strong"
        >
          Configure what this project actually needs
        </h2>
        <p class="mt-1 text-xs text-fg-muted">
          These areas are not blanket requirements. Open them when they match the work your agents will perform.
        </p>
      </div>
      <ul class="grid gap-3 sm:grid-cols-2">
        <li
          v-for="link in setupLinks"
          :key="link.key"
        >
          <RouterLink
            :to="link.to"
            class="focus-ring group flex h-full items-start gap-3 rounded-lg border border-subtle bg-bg-surface p-4 transition hover:border-strong hover:bg-bg-surface-alt"
          >
            <UiMedallion
              :icon="link.icon"
              shape="square"
            />
            <div class="min-w-0 flex-1">
              <div class="flex items-center justify-between gap-3">
                <span class="text-sm font-semibold text-fg-strong">{{ link.label }}</span>
                <UiIcon
                  name="arrow-right"
                  class="h-4 w-4 text-fg-subtle"
                  aria-hidden="true"
                />
              </div>
              <p class="mt-1 text-xs leading-5 text-fg-muted">
                {{ link.description }}
              </p>
            </div>
          </RouterLink>
        </li>
      </ul>
    </section>

    <details class="rounded-lg border border-subtle bg-bg-surface-alt p-4">
      <summary class="focus-ring cursor-pointer rounded-sm text-sm font-medium text-fg-strong">
        Technical runtime details
      </summary>
      <dl class="mt-4 grid gap-3 text-xs sm:grid-cols-3">
        <div>
          <dt class="text-fg-subtle">
            StackOS version
          </dt>
          <dd class="mt-1 font-mono text-fg-default">
            {{ readiness.version ?? 'Unavailable' }}
          </dd>
        </div>
        <div>
          <dt class="text-fg-subtle">
            Runtime uptime
          </dt>
          <dd class="mt-1 font-mono text-fg-default">
            {{ uptimeLabel ?? 'Unavailable' }}
          </dd>
        </div>
        <div>
          <dt class="text-fg-subtle">
            Derived checks
          </dt>
          <dd class="mt-1 font-mono text-fg-default">
            {{ readiness.checks.length }}
          </dd>
        </div>
      </dl>
    </details>
  </div>
</template>
