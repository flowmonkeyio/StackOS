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

import { ActivityItem, AttentionItemRow, ProjectPageHeader } from '@/components/domain'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiButton,
  UiCard,
  UiCallout,
  UiCountBadge,
  UiEmptyState,
  UiIcon,
  UiPageShell,
  UiSkeleton,
} from '@/components/ui'
import { apiFetch } from '@/lib/client'
import { readinessTone, useReadinessStore } from '@/stores/readiness'
import { useAttentionStore } from '@/stores/attention'
import { usePolling } from '@/composables/usePolling'
import {
  formatAbsoluteDateTime,
  formatDurationBetween,
  formatRelativeDateTime,
  newestFirst,
} from '@/lib/stackos/time'
import type { SchemaPageResponseProjectEventOut, SchemaPageResponseRunOut, SchemaRunOut } from '@/api'

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
      `/api/v1/projects/${id}/context/timeline?limit=12&order=desc`,
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
  await Promise.all([
    attention.refresh(id),
    readiness.refresh(id),
    loadRunning(id),
    loadTimeline(id),
  ])
  loaded.value = true
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

const topAttention = computed(() => attention.items.slice(0, 5))
const moreAttention = computed(() => Math.max(0, attention.items.length - topAttention.value.length))
const showReadiness = computed(() => readiness.checks.length > 0)

function runTitle(run: SchemaRunOut): string {
  if (run.last_step) return run.last_step
  return run.kind ? `${run.kind} run` : 'Agent run'
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

    <!-- Readiness banner: stable across polls; reserve height before first load -->
    <UiSkeleton
      v-if="!showReadiness"
      shape="block"
      height="2.75rem"
    />
    <UiCallout
      v-else-if="!readiness.ready"
      :tone="readiness.blocker ? readinessTone(readiness.blocker.state) : 'warning'"
      :title="readiness.headline"
    >
      {{ readiness.blocker?.hint ?? 'Some setup is still needed before agents can fully operate.' }}
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          @click="$router.push(readiness.blocker?.to ?? `${base}/setup`)"
        >
          {{ readiness.blocker?.to ? 'Resolve' : 'Open setup' }}
        </UiButton>
      </template>
    </UiCallout>
    <div
      v-else
      class="flex items-center gap-2.5 rounded-lg border border-success-border bg-success-subtle px-3.5 py-2.5 text-sm text-success-fg"
    >
      <UiIcon
        name="shield-check"
        class="h-4 w-4 shrink-0"
        aria-hidden="true"
      />
      <span class="font-medium">Ready to run agent work</span>
      <span
        v-if="readiness.version"
        class="text-2xs opacity-80"
      >· StackOS v{{ readiness.version }}</span>
    </div>

    <div class="grid items-start gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,1fr)]">
      <!-- Left column -->
      <div class="space-y-5">
        <!-- Needs you -->
        <UiCard
          section
          :padded="false"
          class="overflow-hidden"
        >
          <template #header>
            <div class="flex items-center gap-2">
              <h2 class="t-h3 text-fg-strong">
                Needs you
              </h2>
              <UiCountBadge
                v-if="attention.total > 0"
                :value="attention.total"
                tone="danger"
              />
            </div>
            <UiButton
              variant="ghost"
              size="sm"
              icon-right="arrow-right"
              @click="$router.push(`${base}/inbox`)"
            >
              Inbox
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
              height="2.75rem"
            />
          </div>
          <div
            v-else-if="attention.items.length === 0"
            class="px-4 py-6"
          >
            <UiEmptyState
              icon="check-circle"
              title="Nothing needs you right now"
              description="Approvals, questions, blockers, and failures will appear here."
              size="sm"
            />
          </div>
          <div v-else>
            <div class="divide-y divide-border-subtle">
              <AttentionItemRow
                v-for="item in topAttention"
                :key="item.id"
                :item="item"
              />
            </div>
            <div
              v-if="moreAttention > 0 || attention.degraded"
              class="flex items-center justify-between gap-2 border-t border-border-subtle px-4 py-2.5"
            >
              <UiButton
                v-if="moreAttention > 0"
                variant="link"
                size="sm"
                @click="$router.push(`${base}/inbox`)"
              >
                {{ moreAttention }} more in your inbox
              </UiButton>
              <span
                v-if="attention.degraded"
                class="text-2xs text-fg-subtle"
              >Some signals couldn’t be loaded.</span>
            </div>
          </div>
        </UiCard>

        <!-- Agents at work -->
        <UiCard
          section
          :padded="false"
          class="overflow-hidden"
        >
          <template #header>
            <div class="flex items-center gap-2">
              <h2 class="t-h3 text-fg-strong">
                Agents at work
              </h2>
              <StatusBadge
                v-if="runningRuns.length > 0"
                status="running"
                kind="run"
                :label="`${runningRuns.length} active`"
                small
              />
            </div>
            <UiButton
              variant="ghost"
              size="sm"
              icon-right="arrow-right"
              @click="$router.push(`${base}/activity`)"
            >
              Activity
            </UiButton>
          </template>

          <div
            v-if="!loaded"
            class="space-y-3 px-4 py-4"
          >
            <UiSkeleton
              v-for="n in 2"
              :key="n"
              shape="block"
              height="2.75rem"
            />
          </div>
          <div
            v-else-if="runningRuns.length === 0"
            class="px-4 py-6"
          >
            <UiEmptyState
              icon="runs"
              title="No agents are working right now"
              description="Live agent runs show here while they’re in progress."
              size="sm"
            />
          </div>
          <ul
            v-else
            class="divide-y divide-border-subtle"
          >
            <li
              v-for="run in runningRuns"
              :key="run.id"
            >
              <RouterLink
                :to="`${base}/runs/${run.id}`"
                class="focus-ring-inset group flex items-center gap-3 px-4 py-3 transition-colors duration-fast hover:bg-bg-surface-alt"
              >
                <span
                  class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-info-subtle text-info-fg"
                  aria-hidden="true"
                >
                  <UiIcon
                    name="loader"
                    class="h-4 w-4 animate-spin"
                  />
                </span>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-sm font-medium text-fg-strong">
                    {{ runTitle(run) }}
                  </p>
                  <p class="mt-0.5 text-2xs text-fg-subtle">
                    Running for {{ formatDurationBetween(run.started_at, null) }}
                    <span
                      v-if="isStale(run)"
                      class="text-warning-fg"
                    >· may be stalled</span>
                  </p>
                </div>
                <UiIcon
                  name="chevron-right"
                  class="h-4 w-4 shrink-0 text-fg-subtle opacity-0 transition-opacity duration-fast group-hover:opacity-100"
                  aria-hidden="true"
                />
              </RouterLink>
            </li>
          </ul>
        </UiCard>
      </div>

      <!-- Right column: Recent activity -->
      <UiCard
        section
        :padded="false"
        class="overflow-hidden"
      >
        <template #header>
          <h2 class="t-h3 text-fg-strong">
            Recent activity
          </h2>
          <UiButton
            variant="ghost"
            size="sm"
            icon-right="arrow-right"
            @click="$router.push(`${base}/activity`)"
          >
            View all
          </UiButton>
        </template>

        <div
          v-if="!loaded"
          class="space-y-3 px-4 py-4"
        >
          <UiSkeleton
            v-for="n in 6"
            :key="n"
            shape="block"
            height="2.75rem"
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
            Activity is unavailable right now.
          </UiCallout>
        </div>
        <div
          v-else-if="timeline.length === 0"
          class="px-4 py-6"
        >
          <UiEmptyState
            icon="list"
            title="No activity yet"
            description="The project’s story appears here as agents work."
            size="sm"
          />
        </div>
        <div
          v-else
          class="divide-y divide-border-subtle"
        >
          <ActivityItem
            v-for="event in timeline"
            :key="event.id"
            :event="event"
            :to="timelineLink(event.run_id)"
          />
        </div>
      </UiCard>
    </div>
  </UiPageShell>
</template>
