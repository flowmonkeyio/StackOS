<script setup lang="ts">
// HomeConsoleView — the project's operations console.
//
// Answers, top to bottom: is it ready? · what needs me? · what are agents
// doing now? · what changed recently? It reads from the derivation stores
// (attention, readiness) and the timeline, refreshed by a visibility-aware
// poll. Everything composes from existing primitives.

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ActivityItem, AttentionItemRow, ProjectPageHeader } from '@/components/domain'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiButton,
  UiCard,
  UiCallout,
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

const STALE_MS = 5 * 60_000

async function loadRunning(id: number): Promise<void> {
  try {
    const page = await apiFetch<SchemaPageResponseRunOut>(
      `/api/v1/projects/${id}/runs?status=running&limit=20`,
    )
    runningRuns.value = newestFirst(page.items, (r) => r.started_at)
  } catch {
    runningRuns.value = []
  }
}

async function loadTimeline(id: number): Promise<void> {
  try {
    const page = await apiFetch<SchemaPageResponseProjectEventOut>(
      `/api/v1/projects/${id}/context/timeline?limit=12&order=desc`,
    )
    timeline.value = newestFirst(page.items, (e) => e.occurred_at ?? e.created_at)
    activityDegraded.value = false
  } catch {
    timeline.value = []
    activityDegraded.value = true
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
}

const { lastRunAt, running, refresh } = usePolling(loadAll, { intervalMs: 20_000 })

const updatedLabel = computed(() =>
  lastRunAt.value ? formatRelativeDateTime(lastRunAt.value.toISOString()) : null,
)

const topAttention = computed(() => attention.items.slice(0, 5))
const moreAttention = computed(() => Math.max(0, attention.items.length - topAttention.value.length))

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
          :loading="running"
          @click="refresh"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>

    <!-- Readiness banner -->
    <UiCallout
      v-if="!readiness.loading && !readiness.ready"
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
      v-else-if="readiness.ready"
      class="flex items-center gap-2.5 rounded-lg border border-success-border bg-success-subtle px-3 py-2 text-sm text-success-fg"
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

    <div class="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,1fr)]">
      <!-- Left column -->
      <div class="space-y-5">
        <!-- Needs you -->
        <UiCard section>
          <template #header>
            <div class="flex items-center gap-2">
              <h2 class="t-h3 text-fg-strong">
                Needs you
              </h2>
              <span
                v-if="attention.total > 0"
                class="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-danger-subtle px-1.5 text-2xs font-semibold text-danger-fg tabular-nums"
              >{{ attention.total }}</span>
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
            v-if="attention.loading && attention.items.length === 0"
            class="space-y-3 py-1"
          >
            <UiSkeleton
              v-for="n in 3"
              :key="n"
              shape="block"
              height="2.5rem"
            />
          </div>
          <UiEmptyState
            v-else-if="attention.items.length === 0"
            icon="check-circle"
            title="Nothing needs you right now"
            description="Approvals, questions, blockers, and failures will appear here."
            size="sm"
          />
          <div v-else>
            <div class="divide-y divide-border-subtle">
              <AttentionItemRow
                v-for="item in topAttention"
                :key="item.id"
                :item="item"
              />
            </div>
            <div
              v-if="moreAttention > 0"
              class="px-2.5 pt-3"
            >
              <UiButton
                variant="link"
                size="sm"
                @click="$router.push(`${base}/inbox`)"
              >
                {{ moreAttention }} more in your inbox
              </UiButton>
            </div>
            <p
              v-if="attention.degraded"
              class="px-2.5 pt-2 text-2xs text-fg-subtle"
            >
              Some signals couldn’t be loaded — this list may be incomplete.
            </p>
          </div>
        </UiCard>

        <!-- Agents at work -->
        <UiCard section>
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

          <UiEmptyState
            v-if="runningRuns.length === 0"
            icon="runs"
            title="No agents are working right now"
            description="Live agent runs show here while they’re in progress."
            size="sm"
          />
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
                class="focus-ring-inset group flex items-center gap-3 rounded-md px-2.5 py-3 transition-colors duration-fast hover:bg-bg-surface-alt"
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
      <UiCard section>
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

        <UiCallout
          v-if="activityDegraded"
          tone="neutral"
          density="compact"
        >
          Activity is unavailable right now.
        </UiCallout>
        <UiEmptyState
          v-else-if="timeline.length === 0"
          icon="list"
          title="No activity yet"
          description="The project’s story appears here as agents work."
          size="sm"
        />
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
