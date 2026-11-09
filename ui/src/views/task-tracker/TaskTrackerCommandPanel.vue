<script setup lang="ts">
import { UiBadge, UiButton, UiIcon, UiInput, UiSegmentedControl, UiSelect, UiToolbar } from '@/components/ui'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import { computed } from 'vue'
import { executionStatus } from '@/lib/task-tracker/execution'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import type { TaskDeliveryState } from '@/lib/task-tracker/delivery'
import type { AgentStatsMetrics } from '@/lib/agentActivity'
import type { TrackerSelectOption, TrackerTaskSelectOption, ViewMode } from './viewTypes'

const props = defineProps<{
  viewMode: ViewMode
  activeTaskKey: string
  taskOptions: TrackerTaskSelectOption[]
  search: string
  workflowFilter: string
  workflowOptions: TrackerSelectOption[]
  assigneeFilter: string
  assigneeOptions: TrackerSelectOption[]
  taskRowsCount: number
  tasksCount: number
  filtersActive: boolean
  taskAvailable: boolean
  taskId?: number
  taskStatus?: string
  completedCount?: number
  totalCount?: number
  activeExecution?: ActiveExecution | null
  taskAgentStats?: AgentStatsMetrics | null
  taskAutomated?: boolean
  taskDelivery?: TaskDeliveryState | null
}>()
defineEmits<{
  (e: 'taskSelect', value: string | number | null): void
  (e: 'update:viewMode', value: ViewMode): void
  (e: 'update:search', value: string): void
  (e: 'update:workflowFilter', value: string): void
  (e: 'update:assigneeFilter', value: string): void
  (e: 'openTaskDetail'): void
  (e: 'clear'): void
}>()
const viewOptions = [{ key: 'board', label: 'Board' }, { key: 'graph', label: 'Graph' }]
const state = computed(() => executionStatus(props.taskStatus ?? 'not-started', props.activeExecution, props.taskDelivery))
</script>

<template>
  <UiToolbar
    aria-label="Work view controls"
    class="tracker-command-panel"
    :class="{ 'tracker-command-panel--graph': viewMode === 'graph' }"
    variant="plain"
  >
    <UiSegmentedControl
      class="tracker-command-panel__views"
      :model-value="viewMode"
      label="Work view"
      :options="viewOptions"
      @select="$emit('update:viewMode', String($event) as ViewMode)"
    />
    <template v-if="viewMode === 'board'">
      <div class="tracker-command-panel__search">
        <UiInput
          :model-value="search"
          type="search"
          placeholder="Search tasks"
          aria-label="Search tasks"
          @update:model-value="$emit('update:search', String($event ?? ''))"
        >
          <template #prefix>
            <UiIcon
              name="search"
              class="h-4 w-4"
            />
          </template>
        </UiInput>
      </div>
      <div class="tracker-command-panel__filter">
        <UiSelect
          :model-value="workflowFilter"
          :options="workflowOptions"
          aria-label="Workflow filter"
          @change="$emit('update:workflowFilter', String($event ?? ''))"
        />
      </div>
      <div class="tracker-command-panel__filter tracker-command-panel__assignee">
        <UiSelect
          :model-value="assigneeFilter"
          :options="assigneeOptions"
          aria-label="Assignee filter"
          @change="$emit('update:assigneeFilter', String($event ?? ''))"
        />
      </div>
      <UiButton
        v-if="filtersActive"
        variant="ghost"
        size="sm"
        @click="$emit('clear')"
      >
        Clear
      </UiButton>
      <span class="tracker-command-panel__count text-xs text-fg-muted">
        {{ filtersActive ? `${taskRowsCount} / ${tasksCount}` : tasksCount }} tasks
      </span>
    </template>
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
        </UiSelect>
      </div>
      <div
        v-if="taskAvailable"
        class="tracker-command-panel__progress"
        :title="taskDelivery?.reason ?? undefined"
      >
        <UiBadge
          :tone="state.tone"
          :dot="true"
          variant="outline"
        >
          {{ state.label }}
        </UiBadge>
        <span>{{ completedCount ?? 0 }}/{{ totalCount ?? 0 }} complete</span>
      </div>
      <AgentUsageSummary
        v-if="taskAvailable && taskAutomated"
        class="tracker-command-panel__usage"
        :metrics="taskAgentStats"
        compact
        inline
        aria-label="Lifetime task usage"
        title="Lifetime task usage"
      />
      <UiButton
        variant="secondary"
        size="sm"
        icon-left="file-text"
        :disabled="!taskAvailable"
        @click="$emit('openTaskDetail')"
      >
        Task details
      </UiButton>
    </div>
  </UiToolbar>
</template>

<style scoped>
.tracker-command-panel { flex: none; flex-wrap: nowrap; min-width: 0; gap: 8px; container-type: inline-size; }
.tracker-command-panel--graph { flex-wrap: wrap; }
.tracker-command-panel__views { flex: none; flex-wrap: nowrap; white-space: nowrap; }
.tracker-command-panel__search { flex: 0 1 280px; min-width: 120px; }
.tracker-command-panel__filter { flex: 0 1 180px; min-width: 110px; }
.tracker-command-panel__assignee { flex-basis: 160px; }
.tracker-command-panel__context { display: grid; grid-template-columns: minmax(140px, 1fr) 120px 354px max-content; align-items: center; gap: 16px; width: 100%; min-width: 0; padding: 10px 14px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); }
.tracker-command-panel__task { flex: 1 1 240px; min-width: 140px; }
.tracker-command-panel__identity { display: flex; align-items: center; gap: 8px; height: 20px; color: var(--color-fg-muted); font-size: 11px; }
.tracker-command-panel__task :deep(.ui-select__button) { border: 0; box-shadow: none; padding-left: 0; font-weight: var(--fw-semibold); }
.tracker-command-panel__task :deep(.ui-select__right-chip), .tracker-command-panel__task :deep(.ui-select__right-meta) { display: none; }
.tracker-command-panel__progress { display: grid; flex: 0 0 120px; gap: 4px; padding-left: 16px; border-left: 1px solid var(--color-border-default); color: var(--color-fg-muted); font-size: 11px; white-space: nowrap; }
.tracker-command-panel__progress :deep(.ui-badge) { border: 0; padding: 0; justify-self: start; background: transparent; }
.tracker-command-panel__count { margin-left: auto; flex: none; white-space: nowrap; }
.tracker-command-panel__usage { flex: 0 0 354px; min-width: 0; padding-left: 16px; border-left: 1px solid var(--color-border-default); }
.tracker-command-panel__context > :last-child { grid-column: 4; margin-left: auto; }
@container (max-width: 850px) {
  .tracker-command-panel__context { display: grid; grid-template-columns: minmax(0, 1fr) 120px; grid-template-rows: 52px 36px; gap: 12px; }
  .tracker-command-panel__task { grid-column: 1; grid-row: 1; }
  .tracker-command-panel__progress { grid-column: 2; grid-row: 1; padding-left: 12px; }
  .tracker-command-panel__usage { grid-column: 1; grid-row: 2; width: min(354px, 100%); padding-left: 0; border-left: 0; }
  .tracker-command-panel__context > :last-child { grid-column: 2; grid-row: 2; }
}
@media (max-width: 1100px) {
  .tracker-command-panel__count { display: none; }
  .tracker-command-panel__filter { flex-basis: 120px; min-width: 95px; }
}
@media (max-width: 800px) {
  .tracker-command-panel { flex-wrap: wrap; }
}
</style>
