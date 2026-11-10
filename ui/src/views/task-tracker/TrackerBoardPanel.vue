<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { UiBadge, UiDropdownMenu, UiIcon } from '@/components/ui'
import { trackerStatus } from '@/design/status'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import ActiveExecutionLine from '@/components/domain/ActiveExecutionLine.vue'
import RunAbortAction from '@/components/domain/RunAbortAction.vue'
import { taskRunAbortTarget } from '@/composables/useRunAbort'
import type { TrackerStatus } from '@/lib/task-tracker/types'
import { isAutomaticDeliveryTask, taskDeliveryState } from '@/lib/task-tracker/delivery'
import { activeExecutionTitle, executionStatus } from '@/lib/task-tracker/execution'
import type { TaskProgressRow } from './viewTypes'
import TaskPriorityBadge from './TaskPriorityBadge.vue'
import { taskPriorities, type TaskPriority } from './taskPriority'
import type { BoardMove } from './useTaskBoardEdits'

const props = defineProps<{
  rows: TaskProgressRow[]; selectedTaskKey?: string; hideEmptyColumns?: boolean
  editPending?: boolean; editError?: string | null; interactionKey?: string
}>()
const emit = defineEmits<{
  (e: 'openTask', key: string): void; (e: 'refresh'): void
  (e: 'move', move: BoardMove): void
  (e: 'priority', edit: { taskKey: string; priority: TaskPriority }): void
}>()
const columns = computed(() => (Object.keys(trackerStatus) as TrackerStatus[]).map((status) => ({
  status,
  rows: props.rows.filter((row) => row.task.status === status),
})).filter(column => !props.hideEmptyColumns || column.rows.length))

function state(row: TaskProgressRow) {
  return executionStatus(row.task.status, row.task.active_execution, taskDeliveryState(row.task))
}
function blocker(row: TaskProgressRow): string | null {
  if (row.task.active_execution) return null
  const titles = row.blockedByTitles
  return titles.length ? `Blocked by: ${titles.join(', ')}` : null
}

const gesture = ref<{ key: string; status: TrackerStatus; fingerprint: string; context?: string } | null>(null)
const insertion = ref<{ anchorKey: string; placement: 'before' | 'after' } | null>(null)
const fingerprint = computed(() => JSON.stringify(props.rows.map(row => [row.task.project_id, row.key, row.task.status, row.task.updated_at])))
const dragging = computed(() => !props.editPending && gesture.value?.fingerprint === fingerprint.value
  && gesture.value?.context === props.interactionKey ? gesture.value : null)

function cancelDrag(): void { gesture.value = null; insertion.value = null }
function onEscape(event: KeyboardEvent): void { if (event.key === 'Escape') cancelDrag() }
onMounted(() => document.addEventListener('keydown', onEscape))
onBeforeUnmount(() => document.removeEventListener('keydown', onEscape))

function startDrag(event: DragEvent, row: TaskProgressRow): void {
  if (props.editPending || !event.dataTransfer) { event.preventDefault(); return }
  gesture.value = { key: row.key, status: row.task.status, fingerprint: fingerprint.value, context: props.interactionKey }
  insertion.value = null
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', row.key)
  const card = (event.target as HTMLElement).closest('.tracker-board__card')
  if (card) event.dataTransfer.setDragImage(card, 20, 20)
}
function overCard(event: DragEvent, row: TaskProgressRow): void {
  if (!dragging.value || dragging.value.status !== row.task.status || dragging.value.key === row.key) { insertion.value = null; return }
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  insertion.value = { anchorKey: row.key, placement: event.clientY < rect.top + rect.height / 2 ? 'before' : 'after' }
}
function drop(event: DragEvent, status: TrackerStatus): void {
  const active = dragging.value
  const target = insertion.value
  if (active && target && active.status === status && props.rows.some(row => row.key === target.anchorKey && row.task.status === status)) {
    event.preventDefault()
    emit('move', { taskKey: active.key, ...target })
  }
  cancelDrag()
}
function moveItems(row: TaskProgressRow) {
  const column = props.rows.filter(item => item.task.status === row.task.status)
  const index = column.findIndex(item => item.key === row.key)
  return [
    { key: 'up', label: 'Move up', disabled: props.editPending || index === 0, onSelect: () => emit('move', { taskKey: row.key, anchorKey: column[index - 1]!.key, placement: 'before' }) },
    { key: 'down', label: 'Move down', disabled: props.editPending || index === column.length - 1, onSelect: () => emit('move', { taskKey: row.key, anchorKey: column[index + 1]!.key, placement: 'after' }) },
  ]
}
function priorityItems(row: TaskProgressRow) {
  return taskPriorities.map(priority => ({ key: priority, label: priority.toUpperCase(), disabled: props.editPending || row.task.priority_key === priority,
    onSelect: () => emit('priority', { taskKey: row.key, priority }) }))
}
</script>

<template>
  <section
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
    >{{ editPending ? 'Saving task…' : dragging ? 'Task lifted. Drop within this column, or press Escape to cancel.' : '' }}</span>
    <section
      v-for="column in columns"
      :key="column.status"
      class="tracker-board__column"
      :aria-label="trackerStatus[column.status].label"
      @drop="drop($event, column.status)"
      @dragleave="!($event.currentTarget as HTMLElement).contains($event.relatedTarget as Node) && (insertion = null)"
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
        <div
          v-for="row in column.rows"
          :key="row.key"
          class="tracker-board__card-wrap"
          :class="{ 'tracker-board__placeholder': dragging?.key === row.key, 'tracker-board__insert-before': dragging && insertion?.anchorKey === row.key && insertion.placement === 'before', 'tracker-board__insert-after': dragging && insertion?.anchorKey === row.key && insertion.placement === 'after' }"
          :data-task-key="row.key"
          @dragover="overCard($event, row)"
        >
          <div
            class="tracker-board__card"
            :class="{ 'tracker-board__card--selected': selectedTaskKey === row.key, 'tracker-board__card--blocked': blocker(row) }"
            :title="row.task.active_execution ? activeExecutionTitle(row.task.active_execution) : undefined"
            :aria-busy="editPending || undefined"
          >
            <span class="tracker-board__identity tracker-board__identity--header">
              <UiDropdownMenu
                :items="moveItems(row)"
                :aria-label="`Move task #${row.id}`"
              >
                <template #trigger="{ toggle, open }">
                  <button
                    type="button"
                    class="tracker-board__grip focus-ring"
                    data-dropdown-trigger
                    :draggable="!editPending"
                    :disabled="editPending"
                    :aria-expanded="open"
                    aria-haspopup="menu"
                    :aria-label="`Move task #${row.id}`"
                    @click="toggle"
                    @dragstart="startDrag($event, row)"
                    @dragend="cancelDrag"
                  >
                    <UiIcon
                      name="dots-vertical"
                      class="tracker-board__grip-dots"
                    /><UiIcon
                      name="dots-vertical"
                      class="tracker-board__grip-dots"
                    />
                  </button>
                </template>
              </UiDropdownMenu>
              <UiDropdownMenu
                :items="priorityItems(row)"
                :aria-label="`Priority for task #${row.id}`"
              >
                <template #trigger="{ toggle, open }">
                  <button
                    type="button"
                    class="tracker-board__priority focus-ring"
                    data-dropdown-trigger
                    :disabled="editPending"
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
            </span>
            <button
              type="button"
              class="tracker-board__open focus-ring"
              :aria-label="`Open task #${row.id}: ${row.task.title}${row.task.active_execution ? `; Active now: ${activeExecutionTitle(row.task.active_execution)}` : ''}`"
              @click="$emit('openTask', row.key)"
            >
              <span
                class="tracker-board__title"
                :title="row.task.title"
              >{{ row.task.title }}</span>
              <span
                class="tracker-board__subtitle"
                :title="taskDeliveryState(row.task)?.reason ?? row.task.goal"
              >{{ taskDeliveryState(row.task)?.reason ?? row.task.goal }}</span>
              <AgentUsageSummary
                v-if="isAutomaticDeliveryTask(row.task)"
                :metrics="row.task.agent_stats"
                :active="!!row.task.active_execution"
                compact
                title="Lifetime task usage"
              />
              <span class="tracker-board__meta">
                <ActiveExecutionLine
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
          <div class="tracker-board__actions">
            <RunAbortAction
              :target="taskRunAbortTarget(row.task)"
              @refresh="$emit('refresh')"
            />
          </div>
        </div>
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
.tracker-board__card-wrap { position: relative; min-width: 0; }
.tracker-board__open { display: flex; flex: 1; min-height: 0; flex-direction: column; align-items: stretch; text-align: left; border-radius: var(--radius-sm); }
.tracker-board__grip, .tracker-board__priority { display: inline-flex; align-items: center; justify-content: center; height: 20px; flex: none; border-radius: var(--radius-xs); }
.tracker-board__grip { position: relative; cursor: grab; width: 14px; }
.tracker-board__grip-dots { position: absolute; top: 2px; width: 16px; height: 16px; left: -4px; }
.tracker-board__grip-dots:last-child { left: 2px; }
.tracker-board__grip:active { cursor: grabbing; }
.tracker-board__priority:disabled, .tracker-board__grip:disabled { cursor: wait; }
.tracker-board__placeholder { opacity: .35; }
.tracker-board__placeholder .tracker-board__card { border-style: dashed; }
.tracker-board__insert-before::before, .tracker-board__insert-after::after { content: ''; position: absolute; z-index: 1; left: 0; right: 0; height: 2px; background: var(--color-border-focus); }
.tracker-board__insert-before::before { top: -12px; }
.tracker-board__insert-after::after { bottom: -12px; }
.tracker-board__message { position: sticky; left: 0; grid-column: 1 / -1; font-size: 12px; }
.tracker-board__actions { position: absolute; top: 8px; right: 8px; }
.tracker-board__card-wrap:has(.ui-dropdown) .tracker-board__identity--header { padding-right: 24px; }
.tracker-board { display: grid; grid-template-columns: repeat(7, minmax(300px, 1fr)); column-gap: 12px; flex: 1 1 auto; min-width: 0; min-height: 360px; overflow: auto; overscroll-behavior: contain; }
.tracker-board__column { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.tracker-board__heading { display: flex; flex: none; align-items: center; gap: 8px; padding: 12px 8px; }
.tracker-board__cards { display: grid; grid-template-columns: minmax(0, 1fr); flex: 1 1 auto; align-content: start; gap: 24px; padding: 4px 4px 16px; overflow-y: auto; min-height: 0; scrollbar-width: thin; scrollbar-gutter: stable; }
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
