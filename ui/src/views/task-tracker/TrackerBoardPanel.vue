<script setup lang="ts">
import { computed } from 'vue'
import { UiBadge, UiIcon } from '@/components/ui'
import { trackerStatus } from '@/design/status'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'
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
          @click="$emit('openTask', row.key)"
        >
          <span class="flex min-w-0 items-center justify-between gap-2">
            <span class="font-mono text-xs text-fg-muted">#{{ row.id }}</span>
            <span
              v-if="row.task.owner"
              class="tracker-board__owner"
              :title="row.task.owner"
            >{{ initials(row.task.owner) }}</span>
          </span>
          <span class="flex min-w-0 items-start gap-2">
            <UiIcon
              :name="trackerStatus[column.status].icon"
              class="mt-0.5 h-4 w-4 shrink-0"
              :style="{ color: `var(--color-${trackerStatus[column.status].tone}-fg)` }"
            />
            <span
              class="tracker-board__title"
              :title="row.task.title"
            >{{ row.task.title }}</span>
          </span>
          <span class="flex min-w-0 flex-wrap items-center gap-1.5">
            <UiBadge
              class="tracker-board__source"
              variant="outline"
              :title="row.workflowLabel"
            >
              <template #iconLeft>
                <UiIcon
                  :name="row.task.source_kind === 'workflow' ? 'git-branch' : 'file-text'"
                  class="h-3.5 w-3.5 shrink-0"
                />
              </template>
              {{ row.workflowLabel }}
            </UiBadge>
            <UiBadge
              v-if="row.blockedCount"
              tone="warning"
            >
              <template #iconLeft>
                <UiIcon
                  name="alert-triangle"
                  class="h-3 w-3 shrink-0"
                />
              </template>
              Blocked
            </UiBadge>
          </span>
          <span class="tracker-board__meta">
            <time
              class="min-w-0 truncate"
              :datetime="row.task.created_at"
              :title="`Created ${formatAbsoluteDateTime(row.task.created_at)}`"
            >Created {{ formatRelativeDateTime(row.task.created_at) }}</time>
            <UiBadge
              v-if="row.totalCount > 0"
              variant="outline"
              class="shrink-0 tabular-nums"
              :title="`${row.completedCount} of ${row.totalCount} tickets complete`"
            >
              <template #iconLeft>
                <UiIcon
                  name="tasks"
                  class="h-3.5 w-3.5 shrink-0"
                />
              </template>
              {{ row.completedCount }}/{{ row.totalCount }}
            </UiBadge>
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
.tracker-board { display: grid; grid-template-columns: repeat(7, minmax(256px, 1fr)); flex: 1 1 auto; min-width: 0; min-height: 360px; overflow: auto; overscroll-behavior: contain; }
.tracker-board__column { min-width: 0; min-height: 0; display: flex; flex-direction: column; border-right: 1px solid var(--color-border-subtle); }
.tracker-board__column:last-child { border-right: 0; }
.tracker-board__heading { display: flex; flex: none; align-items: center; gap: 8px; padding: 12px; }
.tracker-board__cards { display: grid; flex: 1 1 auto; align-content: start; gap: 8px; padding: 0 8px 12px; overflow-y: auto; min-height: 0; }
.tracker-board__card { display: grid; gap: 10px; width: 100%; padding: 12px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); box-shadow: var(--shadow-xs); text-align: left; }
.tracker-board__card:hover { border-color: var(--color-border-strong); background: var(--color-bg-surface-alt); box-shadow: var(--shadow-sm); }
.tracker-board__card--selected { border-color: var(--color-border-focus); }
.tracker-board__title { min-width: 0; color: var(--color-fg-strong); font-size: 14px; font-weight: 500; line-height: 20px; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.tracker-board__owner { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; flex: none; border-radius: var(--radius-full); background: var(--color-accent-primary-subtle); color: var(--color-accent-primary-fg); font-size: 10px; font-weight: 600; }
.tracker-board__source { max-width: 100%; font-size: 11px; font-weight: 400; }
.tracker-board__source :deep(.ui-badge__label) { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.tracker-board__meta { display: flex; min-width: 0; align-items: center; justify-content: space-between; gap: 8px; padding-top: 2px; color: var(--color-fg-muted); font-size: 12px; line-height: 18px; }
.tracker-board__empty { padding: 16px 4px; color: var(--color-fg-subtle); font-size: 12px; text-align: center; }
</style>
