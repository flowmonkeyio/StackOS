<script setup lang="ts">
import { computed } from 'vue'
import { UiIcon } from '@/components/ui'
import { trackerStatus } from '@/design/status'
import { resolveAgentOwnerLogo } from '@/lib/stackos/agentIdentity'
import { formatAbsoluteDateTime, formatShortDate } from '@/lib/stackos/time'
import type { TrackerStatus } from '@/lib/task-tracker/types'
import type { TaskProgressRow } from './viewTypes'

const props = defineProps<{ rows: TaskProgressRow[]; selectedTaskKey?: string }>()
defineEmits<{ (e: 'openTask', key: string): void }>()
const columns = computed(() => (Object.keys(trackerStatus) as TrackerStatus[]).map((status) => ({
  status,
  rows: props.rows.filter((row) => row.task.status === status),
})))

function initials(owner: string): string {
  return owner.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase()
}
</script>

<template>
  <section
    class="tracker-board focus-ring"
    aria-label="Task board"
    tabindex="0"
  >
    <section
      v-for="column in columns"
      :key="column.status"
      class="tracker-board__column"
      :aria-label="trackerStatus[column.status].label"
    >
      <header class="tracker-board__heading">
        <UiIcon
          :name="trackerStatus[column.status].icon"
          class="h-4 w-4 shrink-0"
          :style="{ color: `var(--color-${trackerStatus[column.status].tone}-fg)` }"
        />
        <h2 class="text-sm font-medium text-fg-strong">
          {{ trackerStatus[column.status].label }}
        </h2>
        <span class="text-xs tabular-nums text-fg-muted">{{ column.rows.length }}</span>
      </header>
      <div class="tracker-board__cards">
        <button
          v-for="row in column.rows"
          :key="row.key"
          type="button"
          class="tracker-board__card focus-ring"
          :class="{ 'tracker-board__card--selected': selectedTaskKey === row.key }"
          :aria-label="`Open task #${row.id}: ${row.task.title}`"
          @focus="($event.currentTarget as HTMLElement).scrollIntoView({ block: 'nearest', inline: 'nearest' })"
          @click="$emit('openTask', row.key)"
        >
          <span
            class="tracker-board__title"
            :title="row.task.title"
          >{{ row.task.title }}</span>
          <span class="tracker-board__meta">
            <span class="tracker-board__identity">
              <span class="tracker-board__id">#{{ row.id }}</span>
              <span aria-hidden="true">·</span>
              <time
                :datetime="row.task.created_at"
                :title="`Created ${formatAbsoluteDateTime(row.task.created_at)}`"
              ><span class="sr-only">Created </span>{{ formatShortDate(row.task.created_at) }}</time>
            </span>
            <span
              v-if="row.totalCount > 0"
              class="tracker-board__progress"
              :title="`${row.completedCount} of ${row.totalCount} tickets complete`"
            >
              <UiIcon
                name="tasks"
                class="h-3.5 w-3.5 shrink-0"
              />
              {{ row.completedCount }}/{{ row.totalCount }}
            </span>
            <span
              v-if="row.task.owner"
              class="tracker-board__owner"
              :class="{ 'tracker-board__owner--agent': resolveAgentOwnerLogo(row.task.owner) }"
              :title="row.task.owner"
            >
              <img
                v-if="resolveAgentOwnerLogo(row.task.owner)"
                :src="resolveAgentOwnerLogo(row.task.owner)!"
                :alt="row.task.owner"
                class="tracker-board__owner-logo"
              >
              <template v-else>{{ initials(row.task.owner) }}</template>
            </span>
          </span>
        </button>
        <p
          v-if="!column.rows.length"
          class="tracker-board__empty"
        >
          No tasks
        </p>
      </div>
    </section>
  </section>
</template>

<style scoped>
.tracker-board { display: grid; grid-template-columns: repeat(7, minmax(300px, 1fr)); column-gap: 12px; flex: 1 1 auto; min-width: 0; min-height: 360px; overflow: auto; overscroll-behavior: contain; }
.tracker-board__column { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.tracker-board__heading { display: flex; flex: none; align-items: center; gap: 8px; padding: 12px 8px; }
.tracker-board__cards { display: grid; flex: 1 1 auto; align-content: start; gap: 8px; padding: 4px 4px 12px; overflow-y: auto; min-height: 0; scrollbar-width: thin; }
.tracker-board__card { position: relative; display: grid; gap: 12px; width: 100%; padding: 12px; border: 1px solid transparent; border-radius: var(--radius-lg); background: var(--color-bg-surface); text-align: left; scroll-margin: 4px; }
.tracker-board__card:hover { border-color: var(--color-border-default); background: var(--color-bg-surface-alt); }
.tracker-board__card--selected { border-color: var(--color-border-focus); }
.tracker-board__title { min-width: 0; color: var(--color-fg-strong); font-size: 14px; font-weight: 500; line-height: 20px; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tracker-board__meta { display: flex; min-width: 0; min-height: 20px; align-items: center; gap: 12px; color: var(--color-fg-muted); font-size: 12px; line-height: 18px; }
.tracker-board__identity { display: flex; min-width: 0; align-items: center; gap: 8px; margin-right: auto; white-space: nowrap; }
.tracker-board__identity time { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.tracker-board__id { flex: none; font-variant-numeric: tabular-nums; }
.tracker-board__progress { display: inline-flex; flex: none; align-items: center; gap: 4px; font-variant-numeric: tabular-nums; }
.tracker-board__owner { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; flex: none; border-radius: var(--radius-full); background: var(--color-bg-surface-alt); color: var(--color-fg-muted); font-size: 9px; font-weight: 600; }
.tracker-board__owner--agent { border-radius: var(--radius-xs); background: transparent; }
.tracker-board__owner-logo { width: 100%; height: 100%; border-radius: inherit; object-fit: contain; }
.tracker-board__empty { padding: 16px 4px; color: var(--color-fg-subtle); font-size: 12px; text-align: center; }
</style>
