<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiBadge,
  UiButton,
  UiEmptyState,
  UiIcon,
  UiProgressBar,
  UiSegmentedControl,
} from '@/components/ui'
import { formatRelativeDateTime } from '@/lib/stackos/time'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'

import type { TaskProgressRow } from './viewTypes'

type StoryFilter = 'active' | 'attention' | 'recent' | 'all'

const props = defineProps<{
  projectId: number
  rows: TaskProgressRow[]
  activeTaskKey: string
  focus: string
}>()

const router = useRouter()

const emit = defineEmits<{
  (event: 'select-task', row: TaskProgressRow): void
  (event: 'open-task-detail'): void
}>()

function initialFilter(value: string): StoryFilter {
  if (value === 'blocked') return 'attention'
  if (value === 'recent' || value === 'completed') return 'recent'
  if (value === 'all') return 'all'
  return 'active'
}

const storyFilter = ref<StoryFilter>(initialFilter(props.focus))

const filterOptions = computed(() => [
  {
    key: 'active',
    label: `Active ${props.rows.filter(isActive).length}`,
  },
  {
    key: 'attention',
    label: `Needs attention ${props.rows.filter(needsAttention).length}`,
  },
  {
    key: 'recent',
    label: `Recently finished ${props.rows.filter(isRecent).length}`,
  },
  { key: 'all', label: `All ${props.rows.length}` },
])

const visibleRows = computed(() => {
  if (storyFilter.value === 'active') return props.rows.filter(isActive)
  if (storyFilter.value === 'attention') return props.rows.filter(needsAttention)
  if (storyFilter.value === 'recent') return props.rows.filter(isRecent)
  return props.rows
})

const selectedRow = computed(() =>
  visibleRows.value.find((row) => row.key === props.activeTaskKey) ?? visibleRows.value[0] ?? null,
)

const openTickets = computed(() =>
  selectedRow.value?.tickets.filter((ticket) => !isTerminalTrackerStatus(ticket.status)) ?? [],
)
const attentionTickets = computed(() =>
  openTickets.value.filter((ticket) => ticket.blocker_reason || ticket.blocked_by.length > 0),
)
const latestOutcome = computed(() =>
  selectedRow.value?.tickets
    .filter((ticket) => isTerminalTrackerStatus(ticket.status) && ticket.outcome)
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))[0] ?? null,
)
const currentRunId = computed(() =>
  selectedRow.value?.tickets.find((ticket) => ticket.run_id)?.run_id ?? null,
)

function isActive(row: TaskProgressRow): boolean {
  return row.task.status === 'in-progress' || row.inProgressCount > 0
}

function needsAttention(row: TaskProgressRow): boolean {
  return row.blockedCount > 0 || row.failedCount > 0
}

function isRecent(row: TaskProgressRow): boolean {
  return isTerminalTrackerStatus(row.task.status)
}

function select(row: TaskProgressRow): void {
  emit('select-task', row)
}

function openRun(): void {
  if (!currentRunId.value) return
  void router.push(`/projects/${props.projectId}/runs/${currentRunId.value}`)
}

function openAttention(): void {
  void router.push({
    path: `/projects/${props.projectId}/inbox`,
    query: { focus: 'blocked' },
  })
}

function openActivity(): void {
  void router.push({
    path: `/projects/${props.projectId}/activity`,
    query: { task: selectedRow.value?.key ?? '' },
  })
}
</script>

<template>
  <section
    class="space-y-4"
    aria-label="Work stories"
  >
    <UiSegmentedControl
      v-model="storyFilter"
      :options="filterOptions"
      label="Work story filter"
    />

    <UiEmptyState
      v-if="visibleRows.length === 0"
      icon="check-circle"
      :title="storyFilter === 'active' ? 'No active work' : storyFilter === 'attention' ? 'No work needs attention' : 'No work in this view'"
      :description="storyFilter === 'active' ? 'This project is idle. Work appears here when a connected agent starts it through MCP.' : 'Change the filter to inspect other work.'"
      size="sm"
    />

    <div
      v-else
      class="grid min-h-[520px] overflow-hidden rounded-xl border border-subtle bg-bg-surface xl:grid-cols-[minmax(300px,0.78fr)_minmax(0,1.22fr)]"
    >
      <div class="border-b border-border-subtle xl:border-b-0 xl:border-r">
        <div class="border-b border-border-subtle bg-bg-surface-alt px-4 py-2.5 text-2xs font-medium uppercase tracking-wide text-fg-subtle">
          {{ visibleRows.length }} {{ visibleRows.length === 1 ? 'story' : 'stories' }}
        </div>
        <div class="max-h-[620px] overflow-y-auto">
          <button
            v-for="row in visibleRows"
            :key="row.key"
            type="button"
            class="focus-ring-inset block w-full border-b border-border-subtle px-4 py-4 text-left transition hover:bg-bg-surface-alt"
            :class="selectedRow?.key === row.key ? 'bg-accent-subtle/40' : ''"
            @click="select(row)"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <p class="line-clamp-2 text-sm font-semibold leading-5 text-fg-strong">
                  {{ row.task.title }}
                </p>
                <p class="mt-1 line-clamp-2 text-xs leading-5 text-fg-muted">
                  {{ row.task.goal || row.task.description || 'No goal recorded.' }}
                </p>
              </div>
              <StatusBadge
                :status="row.task.status"
                kind="tracker"
                small
              />
            </div>
            <div class="mt-3 flex flex-wrap items-center gap-2 text-2xs text-fg-subtle">
              <UiBadge
                v-if="row.blockedCount"
                tone="warning"
                size="sm"
              >
                {{ row.blockedCount }} blocked
              </UiBadge>
              <UiBadge
                v-if="row.inProgressCount"
                tone="info"
                size="sm"
              >
                {{ row.inProgressCount }} active
              </UiBadge>
              <span>{{ row.terminalCount }}/{{ row.totalCount }} terminal</span>
              <span>· {{ formatRelativeDateTime(row.task.updated_at) }}</span>
            </div>
          </button>
        </div>
      </div>

      <article
        v-if="selectedRow"
        class="min-w-0 p-5 xl:p-6"
      >
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div class="min-w-0 max-w-3xl">
            <p class="t-overline text-fg-subtle">
              Current work story
            </p>
            <h3 class="mt-1 text-xl font-semibold tracking-tight text-fg-strong">
              {{ selectedRow.task.title }}
            </h3>
            <p class="mt-2 text-sm leading-6 text-fg-muted">
              {{ selectedRow.task.goal || selectedRow.task.description || 'No goal has been recorded.' }}
            </p>
          </div>
          <StatusBadge
            :status="selectedRow.task.status"
            kind="tracker"
          />
        </div>

        <div class="mt-6 grid gap-3 sm:grid-cols-3">
          <div class="rounded-lg border border-subtle bg-bg-surface-alt p-3.5">
            <p class="text-2xs font-medium uppercase tracking-wide text-fg-subtle">
              Where it is now
            </p>
            <p class="mt-2 text-sm font-semibold text-fg-strong">
              {{ selectedRow.currentDetail }}
            </p>
          </div>
          <div class="rounded-lg border border-subtle bg-bg-surface-alt p-3.5">
            <p class="text-2xs font-medium uppercase tracking-wide text-fg-subtle">
              Progress
            </p>
            <p class="mt-2 text-sm font-semibold text-fg-strong">
              {{ selectedRow.terminalCount }} of {{ selectedRow.totalCount }} terminal
            </p>
          </div>
          <div class="rounded-lg border border-subtle bg-bg-surface-alt p-3.5">
            <p class="text-2xs font-medium uppercase tracking-wide text-fg-subtle">
              Owner
            </p>
            <p class="mt-2 text-sm font-semibold text-fg-strong">
              {{ selectedRow.task.owner || 'Originating agent' }}
            </p>
          </div>
        </div>

        <UiProgressBar
          class="mt-4"
          :value="selectedRow.percent"
        />

        <section
          class="mt-6"
          aria-labelledby="story-next-title"
        >
          <div class="flex items-center justify-between gap-3">
            <h4
              id="story-next-title"
              class="text-sm font-semibold text-fg-strong"
            >
              {{ attentionTickets.length ? 'What is blocking progress' : 'What happens next' }}
            </h4>
            <UiBadge
              :tone="attentionTickets.length ? 'warning' : 'neutral'"
              size="sm"
            >
              {{ openTickets.length }} open
            </UiBadge>
          </div>
          <div
            v-if="attentionTickets.length"
            class="mt-3 space-y-2"
          >
            <button
              v-for="ticket in attentionTickets.slice(0, 3)"
              :key="ticket.key"
              type="button"
              class="focus-ring flex w-full items-start gap-3 rounded-lg border border-warning-border bg-warning-subtle p-3 text-left"
              @click="$emit('select-task', selectedRow)"
            >
              <UiIcon
                name="warning"
                class="mt-0.5 h-4 w-4 shrink-0 text-warning-fg"
              />
              <span class="min-w-0">
                <span class="block text-sm font-medium text-fg-strong">{{ ticket.title }}</span>
                <span class="mt-1 block text-xs text-fg-muted">
                  {{ ticket.blocker_reason || `Waiting on ${ticket.blocked_by.join(', ')}` }}
                </span>
              </span>
            </button>
          </div>
          <div
            v-else-if="openTickets.length"
            class="mt-3 space-y-2"
          >
            <div
              v-for="ticket in openTickets.slice(0, 3)"
              :key="ticket.key"
              class="flex items-start justify-between gap-3 rounded-lg border border-subtle px-3.5 py-3"
            >
              <div class="min-w-0">
                <p class="text-sm font-medium text-fg-strong">
                  {{ ticket.title }}
                </p>
                <p class="mt-1 text-xs text-fg-muted">
                  {{ ticket.goal || 'The originating agent owns the next transition.' }}
                </p>
              </div>
              <StatusBadge
                :status="ticket.status"
                kind="tracker"
                small
              />
            </div>
          </div>
          <p
            v-else
            class="mt-3 rounded-lg border border-success-border bg-success-subtle p-3.5 text-sm text-success-fg"
          >
            No open tickets remain. Review the recorded outcome and evidence before considering the work complete.
          </p>
        </section>

        <section
          v-if="latestOutcome"
          class="mt-6 rounded-lg border border-subtle p-4"
        >
          <p class="t-overline text-fg-subtle">
            Latest recorded outcome
          </p>
          <p class="mt-2 text-sm font-medium text-fg-strong">
            {{ latestOutcome.title }}
          </p>
          <p class="mt-1 text-xs leading-5 text-fg-muted">
            {{ latestOutcome.outcome }}
          </p>
        </section>

        <div class="mt-6 flex flex-wrap gap-2">
          <UiButton @click="$emit('open-task-detail')">
            Open task details
          </UiButton>
          <UiButton
            v-if="attentionTickets.length"
            variant="secondary"
            icon-left="alert-triangle"
            @click="openAttention"
          >
            Resolve in Attention
          </UiButton>
          <UiButton
            v-else-if="currentRunId"
            variant="secondary"
            icon-left="runs"
            @click="openRun"
          >
            Open owning run
          </UiButton>
          <UiButton
            v-if="latestOutcome"
            variant="secondary"
            icon-left="list"
            @click="openActivity"
          >
            View outcome timeline
          </UiButton>
        </div>
      </article>
    </div>
  </section>
</template>
