<script setup lang="ts">
// HomeConsoleView — the project's operations console.
//
// Answers, top to bottom: is it ready? · what needs me? · what are agents
// doing now? · what changed recently? It reads from the derivation stores
// (attention, readiness) and the timeline, refreshed by a visibility-aware
// poll. Loading state is shown only on first paint; background polls update
// data in place (stable keys, last-good-on-error) so nothing flickers.

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ActivityItem, ProjectPageHeader } from '@/components/domain'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiButton,
  UiBadge,
  UiCard,
  UiCallout,
  UiEmptyState,
  UiIcon,
  UiPageShell,
  UiSkeleton,
} from '@/components/ui'
import { apiFetch } from '@/lib/client'
import type { Tone } from '@/design/status'
import { useReadinessStore } from '@/stores/readiness'
import { useAttentionStore } from '@/stores/attention'
import { usePolling } from '@/composables/usePolling'
import {
  formatAbsoluteDateTime,
  formatDurationBetween,
  formatRelativeDateTime,
  newestFirst,
} from '@/lib/stackos/time'
import type {
  SchemaAuthStatusOut,
  SchemaPageResponseProjectEventOut,
  SchemaPageResponseRunOut,
  SchemaRunOut,
} from '@/api'

const route = useRoute()
const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const base = computed(() => `/projects/${projectId.value}`)

const attention = useAttentionStore()
const readiness = useReadinessStore()

const runningRuns = ref<SchemaRunOut[]>([])
const timeline = ref<SchemaPageResponseProjectEventOut['items']>([])
const activityDegraded = ref(false)
const loaded = ref(false)

const STALE_MS = 5 * 60_000

async function loadRunning(id: number): Promise<void> {
  try {
    const page = await apiFetch<SchemaPageResponseRunOut>(
      `/api/v1/projects/${id}/runs?status=running&limit=20`,
    )
    runningRuns.value = newestFirst(page.items, (r) => r.started_at)
  } catch {
    // Keep the last good list on a transient poll error — no flicker.
  }
}

async function loadTimeline(id: number): Promise<void> {
  try {
    const page = await apiFetch<SchemaPageResponseProjectEventOut>(
      `/api/v1/projects/${id}/context/timeline?limit=40&order=desc`,
    )
    timeline.value = page.items
    activityDegraded.value = false
  } catch {
    if (timeline.value.length === 0) activityDegraded.value = true
  }
}

async function loadAll(): Promise<void> {
  const id = projectId.value
  if (!id || Number.isNaN(id)) return
  const authStatus = apiFetch<SchemaAuthStatusOut>(`/api/v1/projects/${id}/auth/status`)
  // Home needs the immediate health and connection signals. The full action
  // inventory is intentionally owned by Setup; loading it here added a large,
  // blocking integration scan to every Home refresh.
  const readinessRefresh = readiness.refresh(id, { authStatus, includeActions: false })
  await Promise.all([
    attention.refresh(id, { authStatus }),
    loadRunning(id),
    loadTimeline(id),
  ])
  loaded.value = true
  await readinessRefresh
}

const { lastRunAt, refresh } = usePolling(loadAll, { intervalMs: 20_000 })

// Manual-refresh spinner only — background polls must not pulse the button.
const manualBusy = ref(false)
async function manualRefresh(): Promise<void> {
  manualBusy.value = true
  try {
    await refresh()
  } finally {
    manualBusy.value = false
  }
}

const updatedLabel = computed(() =>
  lastRunAt.value ? formatRelativeDateTime(lastRunAt.value.toISOString()) : null,
)

const showReadiness = computed(() => readiness.checks.length > 0)
const primaryAttention = computed(() => attention.items[0] ?? null)

// Home's feed shows MILESTONES, not the per-ticket in-progress churn that
// floods the raw timeline: drop ticket transitions and keep only task
// completions/failures plus knowledge + run events.
const MILESTONE_STATUSES = new Set(['complete', 'completed', 'failed', 'aborted'])
function isMilestone(event: SchemaPageResponseProjectEventOut['items'][number]): boolean {
  const type = event.event_type ?? ''
  if (type === 'tracker.ticket.status_changed') return false
  if (type === 'tracker.task.status_changed') {
    const status = (event.metadata_json as Record<string, unknown> | null)?.new_status
    return typeof status === 'string' ? MILESTONE_STATUSES.has(status) : true
  }
  return true
}
const recentMilestones = computed(() => timeline.value.filter(isMilestone).slice(0, 6))

const projectState = computed<{
  eyebrow: string
  title: string
  detail: string
  action: string
  to: string
  tone: Tone
}>(() => {
  if (!readiness.ready) {
    return {
      eyebrow: 'Setup affects this project',
      title: readiness.headline,
      detail: readiness.blocker?.hint ?? 'Review project setup before agents use every capability.',
      action: readiness.blocker?.to ? 'Resolve setup' : 'Open setup',
      to: readiness.blocker?.to ?? `${base.value}/setup`,
      tone: readiness.blocker?.state === 'blocked' ? 'danger' : 'warning',
    }
  }
  if (primaryAttention.value) {
    return {
      eyebrow: 'Your next decision',
      title: primaryAttention.value.title,
      detail: primaryAttention.value.detail ?? 'Open the item to see its context and owning work.',
      action: primaryAttention.value.cta,
      to: `${base.value}/inbox?item=${encodeURIComponent(primaryAttention.value.id)}`,
      tone: primaryAttention.value.tone,
    }
  }
  if (runningRuns.value.length > 0) {
    const first = runningRuns.value[0]
    return {
      eyebrow: 'Agents are working',
      title: `${runningRuns.value.length} active ${runningRuns.value.length === 1 ? 'run' : 'runs'}`,
      detail: `${runActor(first)} is at ${runTitle(first)}. No human action is currently required.`,
      action: 'Open active work',
      to: `${base.value}/tasks?focus=active`,
      tone: 'info',
    }
  }
  return {
    eyebrow: 'Project state',
    title: 'Ready, with nothing waiting on you',
    detail: 'Connected agents can use the configured project capabilities. New work starts from an agent through MCP.',
    action: 'Review setup',
    to: `${base.value}/setup`,
    tone: 'success',
  }
})

const supervisionItems = computed<Array<{
  label: string
  value: number
  detail: string
  to: string
  tone: Tone
}>>(() => [
  {
    label: 'Needs you',
    value: attention.total,
    detail: attention.total === 1 ? 'decision or repair' : 'decisions and repairs',
    to: `${base.value}/inbox`,
    tone: attention.total > 0 ? 'danger' : 'neutral',
  },
  {
    label: 'Active work',
    value: runningRuns.value.length,
    detail: runningRuns.value.length === 1 ? 'agent run' : 'agent runs',
    to: `${base.value}/tasks?focus=active`,
    tone: runningRuns.value.length > 0 ? 'info' : 'neutral',
  },
  {
    label: 'Blocked',
    value: attention.countsByKind.blocked,
    detail: attention.countsByKind.blocked === 1 ? 'work group' : 'work groups',
    to: `${base.value}/tasks?focus=blocked`,
    tone: attention.countsByKind.blocked > 0 ? 'warning' : 'neutral',
  },
  {
    label: 'Recent outcomes',
    value: recentMilestones.value.length,
    detail: 'latest milestones',
    to: `${base.value}/activity?view=outcomes`,
    tone: 'neutral',
  },
])

function runTitle(run: SchemaRunOut): string {
  if (run.last_step) return run.last_step
  return run.kind ? `${run.kind} run` : 'Agent work'
}

function runActor(run: SchemaRunOut): string {
  const session = run.client_session_id?.toLowerCase() ?? ''
  if (session.includes('codex')) return 'Codex'
  if (session.includes('claude')) return 'Claude'
  if (session.includes('gemini')) return 'Gemini'
  return 'Connected agent'
}

function isStale(run: SchemaRunOut): boolean {
  const beat = run.heartbeat_at ?? run.started_at
  if (!beat) return false
  const ts = Date.parse(beat)
  return !Number.isNaN(ts) && Date.now() - ts > STALE_MS
}

function timelineLink(runId: number | null | undefined): string | null {
  return runId ? `${base.value}/runs/${runId}` : null
}
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      description="Local agent operations at a glance."
      show-project-status
      show-project-meta
    >
      <template #actions>
        <span
          v-if="updatedLabel"
          class="hidden text-2xs text-fg-subtle sm:inline"
          :title="lastRunAt ? formatAbsoluteDateTime(lastRunAt.toISOString()) : undefined"
        >
          Updated {{ updatedLabel }}
        </span>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="manualBusy"
          @click="manualRefresh"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>

    <UiSkeleton
      v-if="!showReadiness"
      shape="block"
      height="12rem"
    />
    <section
      v-else
      class="overflow-hidden rounded-xl border border-strong bg-bg-surface shadow-sm"
      aria-labelledby="project-state-title"
    >
      <div class="grid gap-6 p-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)] lg:p-6">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <UiBadge :tone="projectState.tone">
              {{ projectState.eyebrow }}
            </UiBadge>
            <span
              v-if="readiness.version"
              class="text-2xs text-fg-subtle"
            >StackOS {{ readiness.version }}</span>
          </div>
          <h2
            id="project-state-title"
            class="mt-3 max-w-3xl text-2xl font-semibold tracking-tight text-fg-strong"
          >
            {{ projectState.title }}
          </h2>
          <p class="mt-2 max-w-2xl text-sm leading-6 text-fg-muted">
            {{ projectState.detail }}
          </p>
          <div class="mt-5 flex flex-wrap gap-2">
            <UiButton @click="$router.push(projectState.to)">
              {{ projectState.action }}
            </UiButton>
            <UiButton
              variant="secondary"
              @click="$router.push(`${base}/activity?view=outcomes`)"
            >
              See recent outcomes
            </UiButton>
          </div>
        </div>

        <div class="rounded-lg border border-subtle bg-bg-surface-alt p-4">
          <div class="flex items-center justify-between gap-3">
            <h3 class="text-sm font-semibold text-fg-strong">
              Why this state
            </h3>
            <UiButton
              variant="link"
              size="sm"
              @click="$router.push(`${base}/setup`)"
            >
              Full setup
            </UiButton>
          </div>
          <ul class="mt-3 space-y-3">
            <li
              v-for="check in readiness.checks.slice(0, 4)"
              :key="check.key"
              class="flex items-start gap-2.5"
            >
              <UiIcon
                :name="check.state === 'ready' ? 'check-circle' : check.state === 'blocked' ? 'x-circle' : 'warning'"
                class="mt-0.5 h-4 w-4 shrink-0"
                :class="check.state === 'ready' ? 'text-success-fg' : check.state === 'blocked' ? 'text-danger-fg' : 'text-warning-fg'"
                aria-hidden="true"
              />
              <div class="min-w-0">
                <p class="text-xs font-medium text-fg-strong">
                  {{ check.label }}
                </p>
                <p class="mt-0.5 text-2xs leading-4 text-fg-muted">
                  {{ check.hint }}
                </p>
              </div>
            </li>
          </ul>
        </div>
      </div>
    </section>

    <section aria-labelledby="supervision-title">
      <div class="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2
            id="supervision-title"
            class="t-h3 text-fg-strong"
          >
            Supervision
          </h2>
          <p class="mt-0.5 text-xs text-fg-muted">
            The current project state, organized by the question you need to answer.
          </p>
        </div>
      </div>
      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <RouterLink
          v-for="item in supervisionItems"
          :key="item.label"
          :to="item.to"
          class="focus-ring group rounded-lg border border-subtle bg-bg-surface p-4 transition hover:border-strong hover:bg-bg-surface-alt"
        >
          <div class="flex items-center justify-between gap-3">
            <span class="text-xs font-medium text-fg-muted">{{ item.label }}</span>
            <UiBadge
              :tone="item.tone"
              size="sm"
            >
              {{ item.value }}
            </UiBadge>
          </div>
          <p class="mt-3 text-2xl font-semibold tabular-nums text-fg-strong">
            {{ item.value }}
          </p>
          <p class="mt-1 text-2xs text-fg-subtle">
            {{ item.detail }}
          </p>
        </RouterLink>
      </div>
    </section>

    <div class="grid items-start gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,0.8fr)]">
      <UiCard
        section
        :padded="false"
        class="overflow-hidden"
      >
        <template #header>
          <div>
            <h2 class="t-h3 text-fg-strong">
              Active work
            </h2>
            <p class="mt-0.5 text-2xs text-fg-subtle">
              Work currently owned by connected agents.
            </p>
          </div>
          <UiButton
            variant="ghost"
            size="sm"
            icon-right="arrow-right"
            @click="$router.push(`${base}/tasks?focus=active`)"
          >
            Open Work
          </UiButton>
        </template>

        <div
          v-if="!loaded"
          class="space-y-3 px-4 py-4"
        >
          <UiSkeleton
            v-for="n in 3"
            :key="n"
            shape="block"
            height="3.5rem"
          />
        </div>
        <div
          v-else-if="runningRuns.length === 0"
          class="px-4 py-6"
        >
          <UiEmptyState
            icon="runs"
            title="No active agent work"
            description="This is a healthy idle state. Work appears after a connected agent starts a run through MCP."
            size="sm"
          />
        </div>
        <ul
          v-else
          class="divide-y divide-border-subtle"
        >
          <li
            v-for="run in runningRuns.slice(0, 4)"
            :key="run.id"
          >
            <RouterLink
              :to="`${base}/runs/${run.id}`"
              class="focus-ring-inset group grid gap-2 px-4 py-3.5 transition hover:bg-bg-surface-alt sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
            >
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <p class="truncate text-sm font-semibold text-fg-strong">
                    {{ runTitle(run) }}
                  </p>
                  <StatusBadge
                    status="running"
                    kind="run"
                    label="Active"
                    small
                  />
                  <UiBadge
                    v-if="isStale(run)"
                    tone="warning"
                    size="sm"
                  >
                    May be stalled
                  </UiBadge>
                </div>
                <p class="mt-1 text-xs text-fg-muted">
                  {{ runActor(run) }} · running for {{ formatDurationBetween(run.started_at, null) }}
                </p>
              </div>
              <span class="text-xs font-medium text-fg-link">Inspect run</span>
            </RouterLink>
          </li>
        </ul>
      </UiCard>

      <UiCard
        section
        :padded="false"
        class="overflow-hidden"
      >
        <template #header>
          <div>
            <h2 class="t-h3 text-fg-strong">
              Recent outcomes
            </h2>
            <p class="mt-0.5 text-2xs text-fg-subtle">
              Completed or failed milestones, with raw events kept in Activity.
            </p>
          </div>
          <UiButton
            variant="ghost"
            size="sm"
            icon-right="arrow-right"
            @click="$router.push(`${base}/activity?view=outcomes`)"
          >
            Activity
          </UiButton>
        </template>
        <div
          v-if="!loaded"
          class="space-y-3 px-4 py-4"
        >
          <UiSkeleton
            v-for="n in 4"
            :key="n"
            shape="block"
            height="3rem"
          />
        </div>
        <div
          v-else-if="activityDegraded"
          class="px-4 py-4"
        >
          <UiCallout
            tone="neutral"
            density="compact"
          >
            Outcomes are unavailable right now.
          </UiCallout>
        </div>
        <div
          v-else-if="recentMilestones.length === 0"
          class="px-4 py-6"
        >
          <UiEmptyState
            icon="check-circle"
            title="No recent outcomes"
            description="Completed work, failures, and recorded decisions appear here."
            size="sm"
          />
        </div>
        <div
          v-else
          class="divide-y divide-border-subtle"
        >
          <ActivityItem
            v-for="event in recentMilestones.slice(0, 5)"
            :key="event.id"
            :event="event"
            :to="timelineLink(event.run_id)"
          />
        </div>
      </UiCard>
    </div>
  </UiPageShell>
</template>
