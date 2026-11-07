<script setup lang="ts">
import { UiBadge, UiButton, UiIcon, UiInput, UiSegmentedControl, UiSelect, UiToolbar } from '@/components/ui'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import type { AgentStatsMetrics } from '@/lib/agentActivity'
import type { TrackerSelectOption, TrackerTaskSelectOption, ViewMode } from './viewTypes'

defineProps<{
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
  graphSummary: string
  taskAgentStats?: AgentStatsMetrics | null
  taskAutomated?: boolean
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
</script>

<template>
  <UiToolbar
    aria-label="Work view controls"
    class="tracker-command-panel"
    :class="{ 'tracker-command-panel--graph': viewMode === 'graph' }"
    :variant="viewMode === 'board' ? 'plain' : 'default'"
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
    <template v-else>
      <div class="tracker-command-panel__task">
        <UiSelect
          :model-value="activeTaskKey"
          :options="taskOptions"
          searchable
          search-placeholder="Find a task"
          placeholder="Select a task"
          aria-label="Selected task"
          @change="$emit('taskSelect', $event)"
        />
      </div>
      <UiBadge v-if="taskAvailable && taskAutomated">
        Automated
      </UiBadge>
      <div class="tracker-command-panel__usage-actions">
        <div
          v-if="taskAvailable"
          class="tracker-command-panel__usage"
          aria-label="Lifetime task usage"
        >
          <AgentUsageSummary
            :metrics="taskAgentStats"
            compact
            title="Lifetime task usage. Cache is included in Input."
          />
        </div>
        <span class="tracker-command-panel__count tracker-command-panel__graph-count text-xs text-fg-muted">{{ graphSummary }}</span>
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
    </template>
  </UiToolbar>
</template>

<style scoped>
.tracker-command-panel { flex: none; flex-wrap: nowrap; min-width: 0; gap: 8px; }
.tracker-command-panel--graph { flex-wrap: wrap; }
.tracker-command-panel__views { flex: none; flex-wrap: nowrap; white-space: nowrap; }
.tracker-command-panel__search { flex: 0 1 280px; min-width: 120px; }
.tracker-command-panel__filter { flex: 0 1 180px; min-width: 110px; }
.tracker-command-panel__assignee { flex-basis: 160px; }
.tracker-command-panel__task { flex: 1 1 240px; min-width: 160px; }
.tracker-command-panel__count { margin-left: auto; flex: none; white-space: nowrap; }
.tracker-command-panel__usage-actions { display: flex; flex: 1 1 auto; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.tracker-command-panel__usage { flex: 1 1 280px; min-width: 240px; }
@media (max-width: 1350px) {
  .tracker-command-panel__graph-count { display: none; }
}
@media (max-width: 1100px) {
  .tracker-command-panel__count { display: none; }
  .tracker-command-panel__filter { flex-basis: 120px; min-width: 95px; }
}
@media (max-width: 800px) {
  .tracker-command-panel { flex-wrap: wrap; }
}
</style>
