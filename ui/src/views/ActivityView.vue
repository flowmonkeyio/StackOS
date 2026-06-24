<script setup lang="ts">
// ActivityView — the project's story: what agents have been doing, newest
// first. A human-readable feed over the durable timeline (order=desc), with a
// light category filter and "load more". The raw run/audit tables live in the
// demoted Developer area, linked from here. First-load skeletons only;
// background polls and transient errors never blank the feed.

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ActivityItem, ProjectPageHeader } from '@/components/domain'
import {
  UiButton,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiSegmentedControl,
  UiSkeleton,
  UiToolbar,
} from '@/components/ui'
import { apiFetch } from '@/lib/client'
import { usePolling } from '@/composables/usePolling'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'
import type { SchemaPageResponseProjectEventOut, SchemaProjectEventOut } from '@/api'

const route = useRoute()
const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const base = computed(() => `/projects/${projectId.value}`)

const PAGE_SIZE = 30

type Category = 'all' | 'work' | 'knowledge' | 'messages' | 'runs'

const filter = ref<Category>('all')
const filterOptions = [
  { key: 'all', label: 'All' },
  { key: 'work', label: 'Work' },
  { key: 'knowledge', label: 'Knowledge' },
  { key: 'messages', label: 'Messages' },
  { key: 'runs', label: 'Runs' },
]

const events = ref<SchemaProjectEventOut[]>([])
const cursor = ref<number | null>(null)
const hasMore = ref(false)
const loadingMore = ref(false)
const degraded = ref(false)
const loaded = ref(false)

function categoryOf(event: SchemaProjectEventOut): Category {
  const type = event.event_type ?? ''
  const source = event.source_type ?? ''
  if (type.startsWith('tracker.')) return 'work'
  if (
    type.startsWith('learning.') ||
    type.startsWith('decision.') ||
    type.startsWith('experiment.') ||
    type.startsWith('context.')
  )
    return 'knowledge'
  if (source === 'run' || source === 'run_plan' || type.startsWith('run.')) return 'runs'
  if (source === 'communication' || source === 'ingress' || /telegram|slack|message/i.test(type))
    return 'messages'
  return 'work'
}

const visibleEvents = computed(() =>
  filter.value === 'all'
    ? events.value
    : events.value.filter((event) => categoryOf(event) === filter.value),
)

function timelineLink(runId: number | null | undefined): string | null {
  return runId ? `${base.value}/runs/${runId}` : null
}

async function fetchPage(after: number | null): Promise<SchemaPageResponseProjectEventOut | null> {
  const id = projectId.value
  if (!id || Number.isNaN(id)) return null
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), order: 'desc' })
  if (after !== null) params.set('after', String(after))
  try {
    const page = await apiFetch<SchemaPageResponseProjectEventOut>(
      `/api/v1/projects/${id}/context/timeline?${params.toString()}`,
    )
    degraded.value = false
    return page
  } catch {
    degraded.value = true
    return null
  }
}

async function load(): Promise<void> {
  const page = await fetchPage(null)
  if (page) {
    events.value = page.items
    cursor.value = page.next_cursor ?? null
    hasMore.value = page.next_cursor != null
  }
  // On a transient error after the first load we keep the current feed.
  loaded.value = true
}

async function loadMore(): Promise<void> {
  if (loadingMore.value || cursor.value == null) return
  loadingMore.value = true
  try {
    const page = await fetchPage(cursor.value)
    if (page) {
      events.value = [...events.value, ...page.items]
      cursor.value = page.next_cursor ?? null
      hasMore.value = page.next_cursor != null
    }
  } finally {
    loadingMore.value = false
  }
}

const { lastRunAt, refresh } = usePolling(load, { intervalMs: 30_000 })

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
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Activity"
      description="What agents have been doing, newest first."
      :breadcrumbs="[{ label: 'Activity' }]"
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

    <UiToolbar
      variant="sunken"
      aria-label="Activity filters"
      density="comfortable"
    >
      <UiSegmentedControl
        v-model="filter"
        :options="filterOptions"
        label="Filter activity by category"
        size="sm"
      />
      <template #right>
        <UiButton
          variant="ghost"
          size="sm"
          icon-right="external-link"
          @click="$router.push(`${base}/runs`)"
        >
          Runs audit
        </UiButton>
      </template>
    </UiToolbar>

    <UiCard
      section
      :padded="false"
      class="overflow-hidden"
    >
      <div
        v-if="!loaded"
        class="space-y-3 px-4 py-4"
      >
        <UiSkeleton
          v-for="n in 7"
          :key="n"
          shape="block"
          height="2.75rem"
        />
      </div>

      <div
        v-else-if="degraded && events.length === 0"
        class="px-4 py-4"
      >
        <UiCallout tone="neutral">
          Activity is unavailable right now. Try refreshing.
        </UiCallout>
      </div>

      <div
        v-else-if="visibleEvents.length === 0"
        class="px-4 py-8"
      >
        <UiEmptyState
          icon="list"
          :title="filter === 'all' ? 'No activity yet' : 'Nothing in this category'"
          description="The project’s story appears here as agents work."
        />
      </div>

      <template v-else>
        <div class="divide-y divide-border-subtle">
          <ActivityItem
            v-for="event in visibleEvents"
            :key="event.id"
            :event="event"
            :to="timelineLink(event.run_id)"
          />
        </div>
        <div
          v-if="filter === 'all' && hasMore"
          class="flex justify-center border-t border-border-subtle px-4 py-3"
        >
          <UiButton
            variant="secondary"
            size="sm"
            :loading="loadingMore"
            @click="loadMore"
          >
            Load older activity
          </UiButton>
        </div>
      </template>
    </UiCard>
  </UiPageShell>
</template>
