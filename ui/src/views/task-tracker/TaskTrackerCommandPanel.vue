<script setup lang="ts">
import { UiBadge, UiButton, UiSelect, UiToolbar } from '@/components/ui'
import TrackerBoardFilterBar from './TrackerBoardFilterBar.vue'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import RunAbortAction from '@/components/domain/RunAbortAction.vue'
import type { RunAbortTarget } from '@/composables/useRunAbort'
import UsageLiveIndicator from '@/components/domain/UsageLiveIndicator.vue'
import { computed } from 'vue'
import { executionStatus } from '@/lib/task-tracker/execution'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import type { TaskDeliveryState } from '@/lib/task-tracker/delivery'
import type { AgentStatsMetrics } from '@/lib/agentActivity'
import type { TrackerSelectOption, TrackerTaskSelectOption, ViewMode } from './viewTypes'
import type { BoardFilterClause, BoardFilterField, BoardFilterState } from './boardFilters'

const props = defineProps<{
  viewMode: ViewMode
  activeTaskKey: string
  taskOptions: TrackerTaskSelectOption[]
  workflowOptions: TrackerSelectOption[]
  assigneeOptions: TrackerSelectOption[]
  taskRowsCount: number
  tasksCount: number
  boardFilters: BoardFilterState
  taskAvailable: boolean
  taskId?: number
  taskStatus?: string
  completedCount?: number
  totalCount?: number
  activeExecution?: ActiveExecution | null
  taskAgentStats?: AgentStatsMetrics | null
  taskAutomated?: boolean
  taskDelivery?: TaskDeliveryState | null
  abortTarget?: RunAbortTarget | null
}>()
defineEmits<{
  (e: 'taskSelect', value: string | number | null): void
  (e: 'update:filter', value: BoardFilterClause): void
  (e: 'removeFilter', field: BoardFilterField): void
  (e: 'update:display', changes: Partial<Pick<BoardFilterState, 'completed' | 'hideEmptyColumns'>>): void
  (e: 'openTaskDetail'): void
  (e: 'clear'): void
  (e: 'refresh'): void
}>()
const state = computed(() => executionStatus(props.taskStatus ?? 'not-started', props.activeExecution, props.taskDelivery))
</script>

<template>
  <UiToolbar
    aria-label="Work view controls"
    class="tracker-command-panel"
    :class="{ 'tracker-command-panel--graph': viewMode === 'graph' }"
    variant="plain"
  >
    <TrackerBoardFilterBar
      v-if="viewMode === 'board'"
      :filters="boardFilters"
      :workflow-options="workflowOptions"
      :assignee-options="assigneeOptions"
      :task-rows-count="taskRowsCount"
      :tasks-count="tasksCount"
      @update:filter="$emit('update:filter', $event)"
      @remove-filter="$emit('removeFilter', $event)"
      @update:display="$emit('update:display', $event)"
      @clear="$emit('clear')"
    />
    <div
      v-else
      class="tracker-command-panel__context"
    >
      <div class="tracker-command-panel__task">
        <div class="tracker-command-panel__identity">
          <span v-if="taskId">#{{ taskId }}</span>
          <UiBadge
            v-if="taskAvailable && taskAutomated"
            tone="info"
          >
            Automated
          </UiBadge>
        </div>
        <UiSelect
          :model-value="activeTaskKey"
          :options="taskOptions"
          searchable
          search-placeholder="Find a task"
          placeholder="Select a task"
          aria-label="Selected task"
          @change="$emit('taskSelect', $event)"
        >
          <template #selected="{ label }">
            <span :title="label">{{ label.replace(/^#\d+\s/, '') }}</span>
          </template>
          <template #option="{ option }">
            <span
              class="tracker-command-panel__option"
              :title="`${option.rightLabel}: ${option.label}`"
            >
              <UiBadge
                class="tracker-command-panel__option-status"
                :tone="option.rightTone ?? 'neutral'"
                :dot="true"
                variant="outline"
              >{{ option.rightLabel }}</UiBadge>
              <span class="tracker-command-panel__option-label">{{ option.label }}</span>
            </span>
          </template>
        </UiSelect>
      </div>
      <div
        v-if="taskAvailable"
        class="tracker-command-panel__progress"
        :title="taskDelivery?.reason ?? undefined"
      >
        <span class="tracker-command-panel__state">
          <UiBadge
            :tone="state.tone"
            :dot="true"
            variant="outline"
          >
            {{ state.label }}
          </UiBadge>
          <UsageLiveIndicator :active="!!activeExecution" />
        </span>
        <span>{{ completedCount ?? 0 }}/{{ totalCount ?? 0 }} complete</span>
      </div>
      <AgentUsageSummary
        v-if="taskAvailable && taskAutomated"
        class="tracker-command-panel__usage"
        :metrics="taskAgentStats"
        :active="!!activeExecution"
        compact
        aria-label="Lifetime task usage"
        title="Lifetime task usage"
      />
      <div class="flex items-center gap-1">
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="file-text"
          :disabled="!taskAvailable"
          @click="$emit('openTaskDetail')"
        >
          Task details
        </UiButton>
        <RunAbortAction
          :target="abortTarget ?? null"
          @refresh="$emit('refresh')"
        />
      </div>
    </div>
  </UiToolbar>
</template>

<style scoped>
.tracker-command-panel { flex: none; flex-wrap: nowrap; min-width: 0; gap: 8px; container-type: inline-size; }
.tracker-command-panel--graph { flex-wrap: wrap; }
.tracker-command-panel__context { display: grid; grid-template-columns: minmax(140px, 1fr) 160px 354px max-content; align-items: center; gap: 16px; width: 100%; min-width: 0; padding: 10px 14px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); }
.tracker-command-panel__task { display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; flex: 1 1 240px; min-width: 140px; }
.tracker-command-panel__identity { display: flex; align-items: center; gap: 8px; height: 20px; color: var(--color-fg-muted); font-size: 11px; }
.tracker-command-panel__task :deep(.ui-select__button) { font-weight: var(--fw-semibold); }
.tracker-command-panel__task :deep(.ui-select__right-chip), .tracker-command-panel__task :deep(.ui-select__right-meta) { display: none; }
.tracker-command-panel__option { display: flex; align-items: center; gap: 8px; min-width: 0; }
.tracker-command-panel__option-status { flex: 0 0 82px; justify-content: flex-start; border: 0; padding: 0; font-size: 10px; }
.tracker-command-panel__option-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.tracker-command-panel__progress { display: grid; flex: 0 0 120px; gap: 4px; padding-left: 16px; border-left: 1px solid var(--color-border-default); color: var(--color-fg-muted); font-size: 11px; white-space: nowrap; }
.tracker-command-panel__progress :deep(.ui-badge) { border: 0; padding: 0; justify-self: start; background: transparent; }
.tracker-command-panel__state { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.tracker-command-panel__usage { flex: 0 0 354px; min-width: 0; padding-left: 16px; border-left: 1px solid var(--color-border-default); }
.tracker-command-panel__context > :last-child { grid-column: 4; margin-left: auto; }
@container (max-width: 850px) {
  .tracker-command-panel__context { display: grid; grid-template-columns: minmax(0, 1fr) 160px; grid-template-rows: 56px minmax(36px, auto); gap: 12px; }
  .tracker-command-panel__task { grid-column: 1; grid-row: 1; }
  .tracker-command-panel__progress { grid-column: 2; grid-row: 1; padding-left: 12px; }
  .tracker-command-panel__usage { grid-column: 1; grid-row: 2; width: min(354px, 100%); padding-left: 0; border-left: 0; }
  .tracker-command-panel__context > :last-child { grid-column: 2; grid-row: 2; }
}
</style>
