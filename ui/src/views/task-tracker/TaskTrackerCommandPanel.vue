<script setup lang="ts">
import { computed } from 'vue'

import {
  UiButton,
  UiFormField,
  UiInput,
  UiSegmentedControl,
  UiSelect,
  UiToolbar,
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
  filteredTicketCount: number | null
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
  <UiToolbar
    aria-label="Task tracker controls"
    class="tracker-command-panel !flex-col !items-stretch"
  >
    <div class="flex w-full flex-wrap items-center gap-2">
      <div class="min-w-0 grow basis-72">
        <UiSelect
          :model-value="activeTaskKey"
          :options="taskOptions"
          placeholder="Select task"
          aria-label="Selected task"
          @change="$emit('taskSelect', $event)"
        />
      </div>

      <UiSegmentedControl
        :model-value="viewMode"
        label="Task tracker view"
        :options="viewOptions"
        @select="$emit('update:viewMode', String($event) as ViewMode)"
      />

      <div class="ml-auto flex items-center gap-2">
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="filter"
          :aria-expanded="filtersExpanded"
          @click="$emit('update:filtersExpanded', !filtersExpanded)"
        >
          {{ filterLabel }}
        </UiButton>
        <UiButton
          v-if="filtersActive"
          variant="ghost"
          size="sm"
          @click="$emit('clear')"
        >
          Clear
        </UiButton>
      </div>
    </div>

    <div
      v-if="filtersExpanded"
      class="grid w-full gap-3 border-t border-subtle pt-3 sm:grid-cols-2 xl:grid-cols-4"
    >
      <UiFormField label="Search">
        <UiInput
          :model-value="search"
          placeholder="Task, ticket, owner, assignee"
          @update:model-value="$emit('update:search', String($event ?? ''))"
        />
      </UiFormField>

      <UiFormField label="Status">
        <UiSelect
          :model-value="statusFilter"
          :options="statusSelectOptions"
          @change="$emit('update:statusFilter', String($event ?? 'all') as StatusFilter)"
        />
      </UiFormField>

      <UiFormField label="Workflow">
        <UiSelect
          :model-value="workflowFilter"
          :options="workflowOptions"
          @change="$emit('update:workflowFilter', String($event ?? ''))"
        />
      </UiFormField>

      <UiFormField label="Assignee">
        <UiSelect
          :model-value="assigneeFilter"
          :options="assigneeOptions"
          @change="$emit('update:assigneeFilter', String($event ?? ''))"
        />
      </UiFormField>
    </div>

    <p class="tracker-command-panel__meta w-full border-t border-subtle pt-2 text-xs text-fg-muted">
      <span>
        <span class="font-medium tabular-nums text-fg-default">{{ taskRowsCount }}/{{ tasksCount }}</span>
        tasks
      </span>
      <span v-if="filteredTicketCount !== null">
        <span class="font-medium tabular-nums text-fg-default">{{ filteredTicketCount }}/{{ ticketsCount }}</span>
        tickets
      </span>
      <span v-else>
        <span class="font-medium tabular-nums text-fg-default">{{ ticketsCount }}</span>
        indexed tickets
      </span>
      <span v-if="activeTerminalCount !== null && activeTotalCount !== null">
        <span class="font-medium tabular-nums text-fg-default">{{ activeTerminalCount }}/{{ activeTotalCount }}</span>
        terminal
      </span>
      <span>
        <span class="font-medium tabular-nums text-fg-default">{{ blockedCount }}</span>
        blocked
      </span>
      <span>
        <span class="font-medium tabular-nums text-fg-default">{{ workflowCount }}</span>
        workflows
      </span>
    </p>
  </UiToolbar>
</template>

<style scoped>
.tracker-command-panel {
  flex: none;
}

.tracker-command-panel__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  column-gap: 8px;
  row-gap: 4px;
}

.tracker-command-panel__meta > span + span::before {
  content: '·';
  margin-right: 8px;
  color: var(--color-fg-subtle);
}
</style>
