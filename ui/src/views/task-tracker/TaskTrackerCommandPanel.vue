<script setup lang="ts">
import { computed } from 'vue'

import {
  UiButton,
  UiFormField,
  UiInput,
  UiPanel,
  UiSegmentedControl,
  UiSelect,
} from '@/components/ui'

import type {
  StatusFilter,
  TrackerSelectOption,
  TrackerStatusOption,
  TrackerTaskSelectOption,
  TrackerViewOption,
  ViewMode,
} from './viewTypes'

const props = defineProps<{
  activeTaskKey: string
  taskOptions: TrackerTaskSelectOption[]
  viewMode: ViewMode
  viewOptions: TrackerViewOption[]
  filtersExpanded: boolean
  filterLabel: string
  filtersActive: boolean
  search: string
  statusFilter: StatusFilter
  statusOptions: TrackerStatusOption[]
  workflowFilter: string
  workflowOptions: TrackerSelectOption[]
  assigneeFilter: string
  assigneeOptions: TrackerSelectOption[]
  taskRowsCount: number
  tasksCount: number
  filteredTicketCount: number
  ticketsCount: number
  activeTerminalCount: number | null
  activeTotalCount: number | null
  blockedCount: number
  workflowCount: number
}>()

defineEmits<{
  (e: 'taskSelect', value: string | number | null): void
  (e: 'update:viewMode', value: ViewMode): void
  (e: 'update:filtersExpanded', value: boolean): void
  (e: 'update:search', value: string): void
  (e: 'update:statusFilter', value: StatusFilter): void
  (e: 'update:workflowFilter', value: string): void
  (e: 'update:assigneeFilter', value: string): void
  (e: 'clear'): void
}>()

const statusSelectOptions = computed(() =>
  props.statusOptions.map((option) => ({
    value: option.key,
    label: option.label,
  })),
)
</script>

<template>
  <UiPanel class="tracker-command-panel">
    <div class="tracker-command-panel__primary">
      <UiFormField class="tracker-command-panel__task" label="Task">
        <UiSelect
          :model-value="activeTaskKey"
          :options="taskOptions"
          placeholder="Select active task"
          @change="$emit('taskSelect', $event)"
        />
      </UiFormField>

      <div class="tracker-command-panel__segment tracker-command-panel__view">
        <span>View</span>
        <UiSegmentedControl
          :model-value="viewMode"
          label="Task tracker view"
          :options="viewOptions"
          @select="$emit('update:viewMode', String($event) as ViewMode)"
        />
      </div>

      <UiButton
        class="tracker-command-panel__filters-toggle"
        variant="secondary"
        size="sm"
        :aria-expanded="filtersExpanded"
        @click="$emit('update:filtersExpanded', !filtersExpanded)"
      >
        {{ filterLabel }}
      </UiButton>

      <UiButton
        v-if="filtersActive"
        class="tracker-command-panel__clear"
        variant="ghost"
        size="sm"
        @click="$emit('clear')"
      >
        Clear
      </UiButton>
    </div>

    <div v-if="filtersExpanded" class="tracker-command-panel__filters-panel">
      <UiFormField class="tracker-command-panel__search" label="Search">
        <UiInput
          :model-value="search"
          placeholder="Ticket, task, owner, outcome"
          @update:model-value="$emit('update:search', String($event ?? ''))"
        />
      </UiFormField>

      <UiFormField class="tracker-command-panel__status" label="Status">
        <UiSelect
          :model-value="statusFilter"
          :options="statusSelectOptions"
          @change="$emit('update:statusFilter', String($event ?? 'all') as StatusFilter)"
        />
      </UiFormField>

      <UiFormField class="tracker-command-panel__workflow" label="Workflow">
        <UiSelect
          :model-value="workflowFilter"
          :options="workflowOptions"
          @change="$emit('update:workflowFilter', String($event ?? ''))"
        />
      </UiFormField>

      <UiFormField class="tracker-command-panel__assignee" label="Assignee">
        <UiSelect
          :model-value="assigneeFilter"
          :options="assigneeOptions"
          @change="$emit('update:assigneeFilter', String($event ?? ''))"
        />
      </UiFormField>
    </div>

    <div class="tracker-command-panel__meta">
      <span>{{ taskRowsCount }}/{{ tasksCount }} tasks</span>
      <span>{{ filteredTicketCount }}/{{ ticketsCount }} tickets</span>
      <span v-if="activeTerminalCount !== null && activeTotalCount !== null">
        {{ activeTerminalCount }}/{{ activeTotalCount }} terminal
      </span>
      <span>{{ blockedCount }} blocked</span>
      <span>{{ workflowCount }} workflows</span>
    </div>
  </UiPanel>
</template>

<style scoped>
.tracker-command-panel {
  display: grid;
  flex: none;
  gap: 8px;
  padding: 10px 12px;
}

.tracker-command-panel__primary {
  display: grid;
  grid-template-columns: minmax(24rem, 1fr) minmax(12rem, 16rem) auto auto;
  gap: 10px;
  align-items: end;
  min-width: 0;
}

.tracker-command-panel__filters-panel {
  display: grid;
  grid-template-columns: minmax(16rem, 1.1fr) minmax(26rem, 1.8fr) minmax(12rem, 1fr) minmax(
      12rem,
      1fr
    );
  gap: 10px;
  align-items: end;
  min-width: 0;
  border-top: 1px solid var(--color-border-subtle);
  padding-top: 8px;
}

.tracker-command-panel :deep(.ui-form-field) {
  gap: 6px;
}

.tracker-command-panel :deep(.ui-form-field__label-row) {
  min-height: 14px;
}

.tracker-command-panel :deep(.ui-form-field__label-row label),
.tracker-command-panel__segment > span {
  color: var(--color-fg-muted);
  font-size: 11px;
  font-weight: 700;
  line-height: 14px;
  text-transform: uppercase;
}

.tracker-command-panel__segment {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.tracker-command-panel__search,
.tracker-command-panel__task,
.tracker-command-panel__view {
  min-width: 0;
}

.tracker-command-panel__filters-toggle,
.tracker-command-panel__clear {
  justify-self: end;
  min-height: 32px;
}

.tracker-command-panel__view :deep(.ui-segmented-control) {
  box-sizing: border-box;
  width: 100%;
  height: 32px;
  min-height: 32px;
  flex-wrap: nowrap;
  gap: 2px;
  padding: 1px;
}

.tracker-command-panel__view :deep(.ui-segmented-control button) {
  height: 28px;
  flex: 1 1 0;
  padding: 0 10px;
  font-size: 13px;
  line-height: 1;
}

.tracker-command-panel__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 12px;
  border-top: 1px solid var(--color-border-subtle);
  padding-top: 8px;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
}

@media (max-width: 1180px) {
  .tracker-command-panel__primary {
    grid-template-columns: minmax(18rem, 1fr) minmax(11rem, 14rem) auto auto;
  }

  .tracker-command-panel__filters-panel {
    grid-template-columns: minmax(16rem, 1fr) minmax(0, 1fr);
  }
}

@media (max-width: 980px) {
  .tracker-command-panel__primary {
    grid-template-columns: minmax(0, 1fr) auto auto;
  }

  .tracker-command-panel__task {
    grid-column: 1 / -1;
  }

  .tracker-command-panel__view {
    grid-column: 1 / 2;
  }
}

@media (max-width: 720px) {
  .tracker-command-panel__primary,
  .tracker-command-panel__filters-panel {
    grid-template-columns: minmax(0, 1fr);
  }

  .tracker-command-panel__search,
  .tracker-command-panel__status,
  .tracker-command-panel__task,
  .tracker-command-panel__workflow,
  .tracker-command-panel__assignee,
  .tracker-command-panel__view,
  .tracker-command-panel__filters-toggle,
  .tracker-command-panel__clear {
    grid-column: 1 / -1;
  }

  .tracker-command-panel__filters-toggle,
  .tracker-command-panel__clear {
    justify-self: start;
  }
}
</style>
