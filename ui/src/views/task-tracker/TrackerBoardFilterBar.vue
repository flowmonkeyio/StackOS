<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiButton, UiFilterBar, UiFormField, UiPopover, UiSelect, UiSwitch } from '@/components/ui'
import BoardFilterEditor from './BoardFilterEditor.vue'
import { boardFilterLabel, completedOptions } from './boardFilters'
import type { BoardFilterClause, BoardFilterField, BoardFilterState } from './boardFilters'
import type { TrackerSelectOption } from './viewTypes'

const props = defineProps<{
  filters: BoardFilterState
  workflowOptions: TrackerSelectOption[]
  assigneeOptions: TrackerSelectOption[]
  taskRowsCount: number
  tasksCount: number
  indexAvailable: boolean
}>()
const emit = defineEmits<{
  (e: 'update:filter', clause: BoardFilterClause): void
  (e: 'removeFilter', field: BoardFilterField): void
  (
    e: 'update:display',
    changes: Partial<Pick<BoardFilterState, 'completed' | 'hideEmptyColumns'>>,
  ): void
  (e: 'clear'): void
}>()
type ChipField = BoardFilterField | 'completed'
const fields: { key: BoardFilterField; label: string }[] = [
  { key: 'text', label: 'Text' },
  { key: 'status', label: 'Status' },
  { key: 'priority', label: 'Priority' },
  { key: 'workflow', label: 'Workflow' },
  { key: 'assignee', label: 'Assignee' },
]
const chips = computed(() => [
  ...props.filters.clauses.map((clause) => ({
    key: clause.field as ChipField,
    label: boardFilterLabel(clause),
  })),
  ...(props.filters.completed === 'all'
    ? []
    : [
        {
          key: 'completed' as const,
          label: `Completed: ${completedOptions.find((option) => option.value === props.filters.completed)?.label}`,
        },
      ]),
])
const bar = ref<InstanceType<typeof UiFilterBar> | null>(null)
const filterOpen = ref(false)
const displayOpen = ref(false)
const editing = ref<BoardFilterField | null>(null)
const editingClause = computed(() =>
  props.filters.clauses.find((clause) => clause.field === editing.value),
)
function remove(key: string): void {
  if (key === 'completed') emit('update:display', { completed: 'all' })
  else emit('removeFilter', key as BoardFilterField)
}
function apply(clause: BoardFilterClause): void {
  emit('update:filter', clause)
  filterOpen.value = false
  bar.value?.closeEditor()
}
function closeMenus(): void {
  filterOpen.value = false
  displayOpen.value = false
}
function toggleFilter(toggle: () => void): void {
  bar.value?.closeEditor()
  displayOpen.value = false
  editing.value = null
  toggle()
}
function toggleDisplay(toggle: () => void): void {
  bar.value?.closeEditor()
  filterOpen.value = false
  toggle()
}
function applyCompleted(value: unknown, close: () => void): void {
  emit('update:display', { completed: value as BoardFilterState['completed'] })
  close()
}
</script>

<template>
  <UiFilterBar
    ref="bar"
    no-search
    aria-label="Board filters"
    class="board-filter-bar"
    :editable-active="chips"
    chip-editor-label="Edit board filter"
    @remove="remove"
    @edit="closeMenus"
  >
    <UiPopover
      v-model="filterOpen"
      aria-label="Filter tasks"
      :width="280"
    >
      <template #trigger="{ toggle, open }">
        <UiButton
          size="sm"
          icon-left="filter"
          data-popover-trigger
          :aria-expanded="open"
          @click="toggleFilter(toggle)"
        >
          Filter
        </UiButton>
      </template>
      <template v-if="editing">
        <UiButton
          variant="ghost"
          size="sm"
          icon-left="chevron-left"
          @click="editing = null"
        >
          Filter by
        </UiButton>
        <p class="px-2 pt-1 text-sm font-medium text-fg-strong">
          {{ fields.find((field) => field.key === editing)?.label ?? 'Contains tickets' }}
        </p>
        <BoardFilterEditor
          :key="editing"
          :field="editing"
          :clause="editingClause"
          :workflow-options="workflowOptions"
          :assignee-options="assigneeOptions"
          @apply="apply"
        />
      </template>
      <div
        v-else
        class="grid p-1"
      >
        <p class="px-2 py-1 text-xs text-fg-muted">
          Filter by
        </p>
        <UiButton
          v-for="field in fields"
          :key="field.key"
          variant="ghost"
          size="sm"
          align="start"
          @click="editing = field.key"
        >
          {{ field.label }}
        </UiButton>
      </div>
    </UiPopover>
    <template #editor="{ filterKey, close }">
      <div
        v-if="filterKey === 'completed'"
        class="p-3"
      >
        <UiFormField label="Completed tasks">
          <UiSelect
            :model-value="filters.completed"
            :options="completedOptions"
            size="sm"
            @change="applyCompleted($event, close)"
          />
        </UiFormField>
      </div>
      <BoardFilterEditor
        v-else
        :key="filterKey"
        :field="filterKey as BoardFilterField"
        :clause="filters.clauses.find((clause) => clause.field === filterKey)"
        :workflow-options="workflowOptions"
        :assignee-options="assigneeOptions"
        @apply="apply"
      />
    </template>
    <template #right>
      <UiButton
        variant="ghost"
        size="sm"
        :disabled="!chips.length"
        @click="$emit('clear')"
      >
        Clear all
      </UiButton>
      <span
        class="board-filter-bar__count text-xs text-fg-muted"
        role="status"
        :aria-label="indexAvailable ? undefined : 'Task count unavailable'"
      >{{ indexAvailable ? `${taskRowsCount} / ${tasksCount} tasks` : '— / — tasks' }}</span>
      <UiPopover
        v-model="displayOpen"
        placement="bottom-end"
        aria-label="Board display"
        :width="260"
      >
        <template #trigger="{ toggle, open }">
          <UiButton
            size="sm"
            variant="ghost"
            icon-left="settings"
            icon-right="chevron-down"
            data-popover-trigger
            :aria-expanded="open"
            @click="toggleDisplay(toggle)"
          >
            Display
          </UiButton>
        </template>
        <div class="space-y-4 p-3">
          <UiFormField label="Completed tasks">
            <UiSelect
              :model-value="filters.completed"
              :options="completedOptions"
              size="sm"
              @change="
                $emit('update:display', { completed: $event as BoardFilterState['completed'] })
              "
            />
          </UiFormField>
          <label class="flex items-center justify-between gap-3 text-sm text-fg-default">Hide empty columns<UiSwitch
            size="sm"
            aria-label="Hide empty columns"
            :model-value="filters.hideEmptyColumns"
            @update:model-value="$emit('update:display', { hideEmptyColumns: $event })"
          /></label>
        </div>
      </UiPopover>
    </template>
  </UiFilterBar>
</template>

<style scoped>
.board-filter-bar {
  position: relative;
  width: 100%;
  min-width: 0;
  padding: 8px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-lg);
  background: var(--color-bg-surface);
}
.board-filter-bar__count {
  display: inline-block;
  min-width: 90px;
  text-align: center;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
</style>
