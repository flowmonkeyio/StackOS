<script setup lang="ts">
import { computed, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'

import { ProjectPageHeader } from '@/components/domain'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiEmptyState,
  UiMedallion,
  UiPageShell,
  UiSegmentedControl,
  UiSkeleton,
} from '@/components/ui'
import { usePolling } from '@/composables/usePolling'
import { apiFetch } from '@/lib/client'
import { eventActor, humanizeEvent, resolveEventVisual } from '@/lib/stackos/events'
import {
  formatAbsoluteDateTime,
  formatRelativeDateTime,
} from '@/lib/stackos/time'
import type { SchemaPageResponseProjectEventOut, SchemaProjectEventOut } from '@/api'

type Category = 'outcomes' | 'work' | 'setup' | 'messages' | 'system' | 'audit'

interface ActivityEpisode {
  id: string
  key: string
  events: SchemaProjectEventOut[]
  latest: SchemaProjectEventOut
  representative: SchemaProjectEventOut
}

const route = useRoute()
const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const base = computed(() => `/projects/${projectId.value}`)
const PAGE_SIZE = 50

function routeCategory(): Category {
  const value = typeof route.query.view === 'string' ? route.query.view : ''
  return ['outcomes', 'work', 'setup', 'messages', 'system', 'audit'].includes(value)
    ? (value as Category)
    : 'outcomes'
}

const filter = ref<Category>(routeCategory())
const filterOptions = [
  { key: 'outcomes', label: 'Outcomes' },
  { key: 'work', label: 'Work' },
  { key: 'setup', label: 'Setup' },
  { key: 'messages', label: 'Messages' },
  { key: 'system', label: 'System' },
  { key: 'audit', label: 'Raw audit' },
]

const events = ref<SchemaProjectEventOut[]>([])
const cursor = ref<number | null>(null)
const hasMore = ref(false)
const loadingMore = ref(false)
const degraded = ref(false)
const loaded = ref(false)
const selectedEpisodeId = ref<string | null>(null)

function metadataStatus(event: SchemaProjectEventOut): string {
  const metadata = event.metadata_json ?? {}
  const status = metadata.new_status ?? metadata.status
  return typeof status === 'string' ? status.toLowerCase() : ''
}

function isOutcome(event: SchemaProjectEventOut): boolean {
  const type = event.event_type.toLowerCase()
  const status = metadataStatus(event)
  if (['complete', 'completed', 'failed', 'aborted', 'skipped'].includes(status)) return true
  return /completed|failed|aborted|decision\.recorded|artifact\.created|learning\.created/.test(type)
}

function categoryMatches(event: SchemaProjectEventOut, category: Category): boolean {
  if (category === 'audit') return true
  if (category === 'outcomes') return isOutcome(event)
  const type = event.event_type.toLowerCase()
  const source = event.source_type.toLowerCase()
  if (category === 'messages') {
    return source === 'communication' || source === 'ingress' || /message|slack|telegram/.test(type)
  }
  if (category === 'setup') {
    return /auth|credential|integration|plugin|schedule|budget|ingress|connection/.test(`${source} ${type}`)
  }
  if (category === 'work') {
    return source === 'run' || source === 'run_plan' || type.startsWith('tracker.') || type.startsWith('run.')
  }
  return !categoryMatches(event, 'messages') && !categoryMatches(event, 'setup') && !categoryMatches(event, 'work')
}

function episodeKey(event: SchemaProjectEventOut): string {
  const taskKey = event.metadata_json?.task_key
  if (typeof taskKey === 'string' && taskKey) return `task:${taskKey}`
  if (event.run_id) return `run:${event.run_id}`
  if (event.source_id) return `${event.source_type}:${event.source_id}`
  return `event:${event.id}`
}

const filteredEvents = computed(() => events.value.filter((event) => categoryMatches(event, filter.value)))

const episodes = computed<ActivityEpisode[]>(() => {
  if (filter.value === 'audit') {
    return filteredEvents.value.map((event) => ({
      id: `episode:${event.id}`,
      key: `event:${event.id}`,
      events: [event],
      latest: event,
      representative: event,
    }))
  }
  const result: ActivityEpisode[] = []
  for (const event of filteredEvents.value) {
    const key = episodeKey(event)
    const previous = result[result.length - 1]
    const previousTime = previous ? Date.parse(previous.latest.occurred_at) : Number.NaN
    const eventTime = Date.parse(event.occurred_at)
    const withinEpisodeWindow =
      !Number.isNaN(previousTime) && !Number.isNaN(eventTime) && Math.abs(previousTime - eventTime) <= 30 * 60_000
    if (previous?.key === key && withinEpisodeWindow) {
      previous.events.push(event)
      const taskEvent = previous.events.find((candidate) =>
        candidate.event_type.startsWith('tracker.task.'),
      )
      if (taskEvent) previous.representative = taskEvent
      continue
    }
    result.push({
      id: `episode:${event.id}`,
      key,
      events: [event],
      latest: event,
      representative: event,
    })
  }
  return result
})

const selectedEpisode = computed(() =>
  episodes.value.find((episode) => episode.id === selectedEpisodeId.value) ?? episodes.value[0] ?? null,
)
const selectedHuman = computed(() =>
  selectedEpisode.value ? humanizeEvent(selectedEpisode.value.representative) : null,
)
const selectedVisual = computed(() =>
  selectedEpisode.value ? resolveEventVisual(selectedEpisode.value.representative) : null,
)
const selectedActor = computed(() =>
  selectedEpisode.value ? eventActor(selectedEpisode.value.representative) : null,
)

function setFilter(value: string | number): void {
  filter.value = value as Category
  selectedEpisodeId.value = null
}

onBeforeRouteUpdate((to) => {
  const value = typeof to.query.view === 'string' ? to.query.view : ''
  filter.value = ['outcomes', 'work', 'setup', 'messages', 'system', 'audit'].includes(value)
    ? (value as Category)
    : 'outcomes'
  selectedEpisodeId.value = null
})

function timelineLink(event: SchemaProjectEventOut): string | null {
  return event.run_id ? `${base.value}/runs/${event.run_id}` : null
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
      description="Understand outcomes first, then expand the exact audit trail when you need it."
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

    <div class="flex flex-wrap items-center justify-between gap-3">
      <UiSegmentedControl
        :model-value="filter"
        :options="filterOptions"
        label="Activity view"
        @update:model-value="setFilter"
      />
      <p class="text-xs text-fg-muted">
        {{ filter === 'audit' ? 'Every stored event is shown separately.' : 'Related adjacent changes are grouped into one episode.' }}
      </p>
    </div>

    <div
      v-if="!loaded"
      class="space-y-3"
    >
      <UiSkeleton
        v-for="n in 6"
        :key="n"
        shape="block"
        height="4rem"
      />
    </div>

    <UiCallout
      v-else-if="degraded && events.length === 0"
      tone="neutral"
    >
      Activity is unavailable right now. Try refreshing.
    </UiCallout>

    <UiEmptyState
      v-else-if="episodes.length === 0"
      icon="list"
      :title="filter === 'outcomes' ? 'No recent outcomes' : 'Nothing in this view'"
      description="Choose Raw audit to inspect every stored event, or wait for agents and people to change project state."
    />

    <div
      v-else
      class="grid min-h-[560px] overflow-hidden rounded-xl border border-subtle bg-bg-surface xl:grid-cols-[minmax(320px,0.9fr)_minmax(0,1.1fr)]"
    >
      <section
        class="border-b border-border-subtle xl:border-b-0 xl:border-r"
        aria-label="Activity episodes"
      >
        <div class="border-b border-border-subtle bg-bg-surface-alt px-4 py-2.5 text-2xs font-medium uppercase tracking-wide text-fg-subtle">
          {{ episodes.length }} {{ episodes.length === 1 ? 'episode' : 'episodes' }}
        </div>
        <div class="max-h-[680px] overflow-y-auto">
          <button
            v-for="episode in episodes"
            :key="episode.id"
            type="button"
            class="focus-ring-inset block w-full border-b border-border-subtle px-4 py-3.5 text-left transition hover:bg-bg-surface-alt"
            :class="selectedEpisode?.id === episode.id ? 'bg-accent-subtle/40' : ''"
            @click="selectedEpisodeId = episode.id"
          >
            <div class="flex items-start gap-3">
              <UiMedallion
                :icon="resolveEventVisual(episode.representative).icon"
                :tone="resolveEventVisual(episode.representative).tone"
              />
              <div class="min-w-0 flex-1">
                <div class="flex items-start justify-between gap-3">
                  <p class="line-clamp-2 text-sm font-semibold text-fg-strong">
                    {{ humanizeEvent(episode.representative).title }}
                  </p>
                  <span class="shrink-0 text-2xs text-fg-subtle">
                    {{ formatRelativeDateTime(episode.latest.occurred_at) }}
                  </span>
                </div>
                <p
                  v-if="humanizeEvent(episode.representative).summary"
                  class="mt-1 line-clamp-2 text-xs leading-5 text-fg-muted"
                >
                  {{ humanizeEvent(episode.representative).summary }}
                </p>
                <div class="mt-2 flex flex-wrap items-center gap-2">
                  <UiBadge
                    tone="neutral"
                    size="sm"
                  >
                    {{ resolveEventVisual(episode.representative).label }}
                  </UiBadge>
                  <UiBadge
                    v-if="episode.events.length > 1"
                    tone="accent"
                    size="sm"
                  >
                    {{ episode.events.length }} related changes
                  </UiBadge>
                </div>
              </div>
            </div>
          </button>
        </div>
        <div
          v-if="hasMore"
          class="border-t border-border-subtle p-3 text-center"
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
      </section>

      <article
        v-if="selectedEpisode && selectedHuman && selectedVisual"
        class="min-w-0 p-5 xl:p-6"
      >
        <div class="flex items-start gap-3">
          <UiMedallion
            :icon="selectedVisual.icon"
            :tone="selectedVisual.tone"
          />
          <div class="min-w-0 flex-1">
            <p class="t-overline text-fg-subtle">
              {{ selectedVisual.label }}
            </p>
            <h2 class="mt-1 text-xl font-semibold tracking-tight text-fg-strong">
              {{ selectedHuman.title }}
            </h2>
            <p
              v-if="selectedHuman.summary"
              class="mt-2 text-sm leading-6 text-fg-muted"
            >
              {{ selectedHuman.summary }}
            </p>
          </div>
        </div>

        <div class="mt-6 grid gap-3 sm:grid-cols-3">
          <div class="rounded-lg border border-subtle bg-bg-surface-alt p-3.5">
            <p class="t-overline text-fg-subtle">
              When
            </p>
            <p class="mt-2 text-sm font-medium text-fg-strong">
              {{ formatAbsoluteDateTime(selectedEpisode.latest.occurred_at) }}
            </p>
          </div>
          <div class="rounded-lg border border-subtle bg-bg-surface-alt p-3.5">
            <p class="t-overline text-fg-subtle">
              Actor
            </p>
            <p class="mt-2 text-sm font-medium text-fg-strong">
              {{ selectedActor || 'Runtime' }}
            </p>
          </div>
          <div class="rounded-lg border border-subtle bg-bg-surface-alt p-3.5">
            <p class="t-overline text-fg-subtle">
              Stored changes
            </p>
            <p class="mt-2 text-sm font-medium text-fg-strong">
              {{ selectedEpisode.events.length }}
            </p>
          </div>
        </div>

        <section
          v-if="selectedEpisode.events.length > 1"
          class="mt-6"
        >
          <h3 class="text-sm font-semibold text-fg-strong">
            Episode history
          </h3>
          <ol class="mt-3 space-y-2 border-l border-border-strong pl-4">
            <li
              v-for="event in selectedEpisode.events"
              :key="event.id"
              class="relative"
            >
              <span class="absolute -left-[1.18rem] top-1.5 h-2 w-2 rounded-full bg-accent-solid" />
              <p class="text-sm font-medium text-fg-strong">
                {{ humanizeEvent(event).title }}
              </p>
              <p class="mt-0.5 text-2xs text-fg-subtle">
                {{ formatAbsoluteDateTime(event.occurred_at) }}
              </p>
            </li>
          </ol>
        </section>

        <div class="mt-6 flex flex-wrap gap-2">
          <UiButton
            v-if="timelineLink(selectedEpisode.latest)"
            @click="$router.push(timelineLink(selectedEpisode.latest)!)"
          >
            Open owning run
          </UiButton>
          <UiButton
            variant="secondary"
            @click="filter = 'audit'"
          >
            Show raw audit
          </UiButton>
        </div>

        <details class="mt-6 rounded-lg border border-subtle bg-bg-surface-alt p-4">
          <summary class="focus-ring cursor-pointer rounded-sm text-sm font-medium text-fg-strong">
            Technical event details
          </summary>
          <dl class="mt-4 grid gap-3 text-xs sm:grid-cols-2">
            <div>
              <dt class="text-fg-subtle">
                Event type
              </dt><dd class="mt-1 font-mono text-fg-default">
                {{ selectedEpisode.latest.event_type }}
              </dd>
            </div>
            <div>
              <dt class="text-fg-subtle">
                Source
              </dt><dd class="mt-1 font-mono text-fg-default">
                {{ selectedEpisode.latest.source_type }}:{{ selectedEpisode.latest.source_id ?? '—' }}
              </dd>
            </div>
            <div>
              <dt class="text-fg-subtle">
                Run
              </dt><dd class="mt-1 font-mono text-fg-default">
                {{ selectedEpisode.latest.run_id ?? '—' }}
              </dd>
            </div>
            <div>
              <dt class="text-fg-subtle">
                Event id
              </dt><dd class="mt-1 font-mono text-fg-default">
                {{ selectedEpisode.latest.id }}
              </dd>
            </div>
          </dl>
          <pre class="mt-4 max-h-72 overflow-auto rounded-md bg-bg-sunken p-3 text-2xs text-fg-muted">{{ JSON.stringify(selectedEpisode.latest.metadata_json ?? {}, null, 2) }}</pre>
        </details>
      </article>
    </div>
  </UiPageShell>
</template>
