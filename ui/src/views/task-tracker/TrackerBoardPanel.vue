<script setup lang="ts">
import { computed, ref } from 'vue'
import { useVisibleMetricDemand } from './useVisibleMetricDemand'
import { UiBadge, UiDropdownMenu, UiIcon, UiReorderList, UiReorderHandle } from '@/components/ui'
import type { DropdownItem } from '@/components/ui/UiDropdownMenu.vue'
import { boardColumnStatus } from '@/design/status'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import ActiveExecutionLine from '@/components/domain/ActiveExecutionLine.vue'
import RunAbortAction from '@/components/domain/RunAbortAction.vue'
import { taskRunAbortTarget } from '@/composables/useRunAbort'
import type { BoardColumn } from '@/lib/task-tracker/types'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'
import { canPlaceTask, type BoardPlacement } from './useTaskBoardEdits'
import { isAutomaticDeliveryTask, taskDeliveryState } from '@/lib/task-tracker/delivery'
import { activeExecutionTitle, executionStatus } from '@/lib/task-tracker/execution'
import type { TaskProgressRow } from './viewTypes'
import TaskPriorityBadge from './TaskPriorityBadge.vue'
import { taskPriorities, type TaskPriority } from './taskPriority'
import type { BoardMove } from './useTaskBoardEdits'

const props = withDefaults(defineProps<{
  rows: TaskProgressRow[]; selectedTaskKey?: string; hideEmptyColumns?: boolean
  editPending?: boolean; editPendingColumns?: string[]; editError?: string | null; interactionKey?: string
  metricsActive?: boolean
  metricDemandEnabled?: boolean
}>(), { metricsActive: true, selectedTaskKey: undefined, editPendingColumns: undefined, editError: null, interactionKey: undefined })
const emit = defineEmits<{
  (e: 'openTask', key: string): void; (e: 'refresh'): void
  (e: 'openGraph', key: string): void
  (e: 'move', move: BoardMove): void
  (e: 'priority', edit: { taskKey: string; priority: TaskPriority }): void
  (e: 'place', edit: { taskKey: string; boardColumn: BoardPlacement }): void
  (e: 'metricDemand', keys: string[]): void
}>()
const root = ref<HTMLElement | null>(null)
const demand = useVisibleMetricDemand(root, '[data-task-key]', node => node.dataset.taskKey,
  keys => emit('metricDemand', keys), () => props.metricsActive !== false)
const demanded = computed(() => new Set(demand.keys.value))
const columns = computed(() => (Object.keys(boardColumnStatus) as BoardColumn[]).map((status) => ({
  status,
  rows: props.rows.filter((row) => row.task.board_column === status),
})).filter(column => !props.hideEmptyColumns || column.rows.length))

function state(row: TaskProgressRow) {
  return executionStatus(row.task.status, isTerminalTrackerStatus(row.task.status) ? null : row.task.active_execution,
    isTerminalTrackerStatus(row.task.status) ? null : taskDeliveryState(row.task))
}
function subtitle(row: TaskProgressRow): string {
  if (row.task.status === 'deferred') {
    const scope = row.task.metadata_json?.delivery_scope
    if (scope && typeof scope === 'object' && 'resume_condition' in scope && typeof scope.resume_condition === 'string') return `Resume when: ${scope.resume_condition}`
  }
  return (isTerminalTrackerStatus(row.task.status) ? null : taskDeliveryState(row.task)?.reason) ?? row.task.goal
}
function blocker(row: TaskProgressRow): string | null {
  if (row.task.active_execution) return null
  const titles = row.blockedByTitles
  return titles.length ? `Blocked by: ${titles.join(', ')}` : null
}

function columnPending(status: string): boolean {
  return props.editPendingColumns?.includes(status) ?? Boolean(props.editPending)
}
const rowKey = (row: TaskProgressRow) => row.key
const rowLabel = (row: TaskProgressRow) => `task #${row.id}`
// Titles, usage, heartbeats and their updated_at values do not change drag eligibility.
const rowVersion = (row: TaskProgressRow) => JSON.stringify([row.task.project_id, row.task.tracker_id, row.task.status, row.task.board_column, row.task.lane_key, row.task.order_index, row.task.priority_key])
function priorityItems(row: TaskProgressRow) {
  const items: DropdownItem[] = taskPriorities.map(priority => ({ key: priority, label: priority.toUpperCase(), disabled: columnPending(row.task.board_column!) || row.task.priority_key === priority,
    onSelect: () => emit('priority', { taskKey: row.key, priority }) }))
  if (canPlaceTask(row.task)) {
    const boardColumn = row.task.board_column === 'backlog' ? 'todo' : 'backlog'
    items.push({ key: 'place', label: `Move to ${boardColumnStatus[boardColumn].label}`, disabled: columnPending(row.task.board_column!),
      onSelect: () => emit('place', { taskKey: row.key, boardColumn }) })
  }
  return [...items, { key: 'details', label: 'Task details', disabled: false, onSelect: () => emit('openTask', row.key) }]
}
</script>

<template>
  <section
    ref="root"
    class="tracker-board focus-ring"
    :style="{ gridTemplateColumns: `repeat(${Math.max(columns.length, 1)}, minmax(300px, 1fr))` }"
    aria-label="Task board"
    tabindex="0"
  >
    <p
      v-if="editError"
      class="tracker-board__message text-danger-fg"
      role="alert"
    >
      {{ editError }}
    </p>
    <span
      class="sr-only"
      role="status"
    >{{ editPending ? 'Saving task…' : '' }}</span>
    <section
      v-for="column in columns"
      :key="column.status"
      class="tracker-board__column"
      :aria-label="boardColumnStatus[column.status].label"
    >
      <header class="tracker-board__heading">
        <UiIcon
          :name="boardColumnStatus[column.status].icon"
          class="h-4 w-4 shrink-0"
          :style="{ color: `var(--color-${boardColumnStatus[column.status].tone}-fg)` }"
        />
        <h2 class="text-sm font-medium text-fg-strong">
          {{ boardColumnStatus[column.status].label }}
        </h2>
        <span class="text-xs tabular-nums text-fg-muted">{{ column.rows.length }}</span>
      </header>
      <UiReorderList
        class="tracker-board__cards"
        :items="column.rows"
        :item-key="rowKey"
        :item-label="rowLabel"
        :item-version="rowVersion"
        :scope-key="`${interactionKey}:${column.status}`"
        :disabled="columnPending(column.status)"
        @move="$emit('move', { taskKey: $event.itemKey, anchorKey: $event.anchorKey, placement: $event.placement })"
      >
        <template #default="{ item: row, handle }">
          <div
            class="tracker-board__card-wrap"
            :data-task-key="row.key"
          >
            <div
              class="tracker-board__card"
              :class="{ 'tracker-board__card--selected': selectedTaskKey === row.key, 'tracker-board__card--blocked': blocker(row) }"
              :title="row.task.active_execution ? activeExecutionTitle(row.task.active_execution) : undefined"
              :aria-busy="columnPending(column.status) || undefined"
            >
              <span class="tracker-board__identity tracker-board__identity--header">
                <UiReorderHandle v-bind="handle" />
                <UiDropdownMenu
                  :items="priorityItems(row)"
                  :aria-label="`Priority for task #${row.id}`"
                >
                  <template #trigger="{ toggle, open }">
                    <button
                      type="button"
                      class="tracker-board__priority focus-ring"
                      data-dropdown-trigger
                      :disabled="columnPending(column.status)"
                      :aria-expanded="open"
                      aria-haspopup="menu"
                      :aria-label="`Change priority for task #${row.id}: ${row.task.priority_key.toUpperCase()}`"
                      @click="toggle"
                    >
                      <TaskPriorityBadge :priority="row.task.priority_key" />
                    </button>
                  </template>
                </UiDropdownMenu>
                <UiBadge
                  v-if="isAutomaticDeliveryTask(row.task)"
                  class="tracker-board__automated"
                  title="Automated"
                  tone="info"
                >
                  Automated
                </UiBadge>
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
                <UiBadge
                  class="tracker-board__state"
                  :tone="state(row).tone"
                  :title="state(row).label"
                  :aria-label="state(row).label"
                  :dot="true"
                  variant="outline"
                >{{ state(row).label }}</UiBadge>
                <RunAbortAction
                  class="tracker-board__actions"
                  :target="taskRunAbortTarget(row.task)"
                  @refresh="$emit('refresh')"
                />
              </span>
              <button
                type="button"
                class="tracker-board__open focus-ring"
                :aria-label="`Open graph for task #${row.id}: ${row.task.title}${row.task.active_execution ? `; Active now: ${activeExecutionTitle(row.task.active_execution)}` : ''}`"
                @click="$emit('openGraph', row.key)"
              >
                <span
                  class="tracker-board__title"
                  :title="row.task.title"
                >{{ row.task.title }}</span>
                <span
                  class="tracker-board__subtitle"
                  :title="subtitle(row)"
                >{{ subtitle(row) }}</span>
                <AgentUsageSummary
                  v-if="metricsActive !== false && isAutomaticDeliveryTask(row.task) && (!metricDemandEnabled || demanded.has(row.key))"
                  :metrics="row.task.agent_stats"
                  :active="!!row.task.active_execution"
                  compact
                  title="Lifetime task usage"
                />
                <span class="tracker-board__meta">
                  <ActiveExecutionLine
                    :metrics-active="metricsActive && (!metricDemandEnabled || demanded.has(row.key))"
                    :item="row.task"
                    :execution="row.task.active_execution"
                    :last-execution="isAutomaticDeliveryTask(row.task) ? row.task.agent_stats?.last_execution : null"
                    :owner="row.task.owner"
                    class="tracker-board__execution"
                  />
                </span>
                <span
                  v-if="blocker(row)"
                  class="tracker-board__blocked"
                  :title="blocker(row)!"
                >{{ blocker(row) }}</span>
              </button>
            </div>
          </div>
        </template>
        <template #empty>
          <p
            v-if="!column.rows.length"
            class="tracker-board__empty"
          >
            No tasks
          </p>
        </template>
      </UiReorderList>
    </section>
  </section>
</template>

<style scoped>
.tracker-board__card-wrap { position: relative; min-width: 0; }
.tracker-board__open { display: flex; flex: 1; min-height: 0; flex-direction: column; align-items: stretch; text-align: left; border-radius: var(--radius-sm); }
.tracker-board__priority { display: inline-flex; align-items: center; justify-content: center; height: 20px; flex: none; border-radius: var(--radius-xs); }
.tracker-board__priority:disabled { cursor: wait; }
.tracker-board__message { position: sticky; left: 0; grid-column: 1 / -1; font-size: 12px; }
.tracker-board__identity--header :deep(.ui-dropdown) { display: inline-flex; align-items: center; flex: none; vertical-align: middle; }
.tracker-board__actions { margin-right: -4px; }
.tracker-board { display: grid; column-gap: 12px; flex: 1 1 auto; min-width: 0; min-height: 360px; overflow: auto; overscroll-behavior: contain; }
.tracker-board__column { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.tracker-board__heading { display: flex; flex: none; align-items: center; gap: 8px; padding: 12px 8px; }
.tracker-board__cards { --reorder-gap: 24px; flex: 1 1 auto; padding: 4px 4px 16px; scrollbar-width: thin; scrollbar-gutter: stable; }
.tracker-board__card { position: relative; display: flex; flex-direction: column; align-items: stretch; justify-content: flex-start; box-sizing: border-box; height: 224px; width: 100%; padding: 12px 12px 54px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); text-align: left; scroll-margin: 4px; }
.tracker-board__card:hover { border-color: var(--color-border-default); background: var(--color-bg-surface-alt); }
.tracker-board__card--selected { border-color: var(--color-border-focus); }
.tracker-board__title { height: 40px; margin-top: 6px; min-width: 0; color: var(--color-fg-strong); font-size: 14px; font-weight: var(--fw-semibold); line-height: 20px; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tracker-board__subtitle { display: block; height: 16px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-fg-muted); font-size: 11px; line-height: 16px; }
.tracker-board__card :deep(.agent-usage-summary) { margin-top: 8px; }
.tracker-board__meta { position: absolute; inset: auto 0 0; height: 56px; display: flex; min-width: 0; align-items: center; padding: 0 12px; border-top: 1px solid var(--color-border-default); }
.tracker-board__execution { flex: 1 1 auto; }
.tracker-board__identity { display: flex; height: 20px; min-width: 0; align-items: center; gap: 5px; white-space: nowrap; color: var(--color-fg-muted); font-size: 10px; }
.tracker-board__identity time { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.tracker-board__identity--header { margin-right: 0; }
.tracker-board__state { margin-left: auto; flex: 0 1 auto; min-width: 26px; }
.tracker-board__automated { flex: 0 1 auto; min-width: 24px; }
.tracker-board__state :deep(.ui-badge__label), .tracker-board__automated :deep(.ui-badge__label) { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.tracker-board__identity :deep(.ui-badge) { font-size: 10px; padding-left: 5px; padding-right: 5px; }
.tracker-board__progress { display: inline-flex; flex: none; align-items: center; gap: 4px; font-variant-numeric: tabular-nums; }
.tracker-board__card--blocked { box-shadow: inset 3px 0 var(--color-warning-default); }
.tracker-board__blocked { position: absolute; bottom: -10px; left: 20px; right: 20px; margin: 0 auto; width: fit-content; max-width: calc(100% - 40px); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 3px 8px; border: 1px solid var(--color-warning-default); border-radius: var(--radius-full); background: var(--color-warning-subtle); color: var(--color-warning-fg); font-size: 10px; line-height: 14px; }
.tracker-board__empty { padding: 16px 4px; color: var(--color-fg-subtle); font-size: 12px; text-align: center; }
</style>
