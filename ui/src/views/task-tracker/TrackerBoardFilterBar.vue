<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import { UiButton, UiFilterBar, UiFormField, UiIcon, UiIconButton, UiPopover, UiSelect, UiSwitch } from '@/components/ui'
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
  (e: 'update:display', changes: Partial<Pick<BoardFilterState, 'completed' | 'hideEmptyColumns'>>): void
  (e: 'clear'): void
}>()
type ChipField = BoardFilterField | 'completed'
const fields: { key: BoardFilterField; label: string }[] = [
  { key: 'text', label: 'Text' }, { key: 'status', label: 'Status' },
  { key: 'priority', label: 'Priority' }, { key: 'workflow', label: 'Workflow' }, { key: 'assignee', label: 'Assignee' },
]
const chips = computed(() => [
  ...props.filters.clauses.map(clause => ({ field: clause.field as ChipField, label: boardFilterLabel(clause) })),
  ...(props.filters.completed === 'all' ? [] : [{ field: 'completed' as const, label: `Completed: ${completedOptions.find(option => option.value === props.filters.completed)?.label}` }]),
])
const filterOpen = ref(false)
const overflowOpen = ref(false)
const displayOpen = ref(false)
const chipOpen = ref(false)
const chipField = ref<ChipField | null>(null)
const chipAnchor = ref<HTMLElement | null>(null)
const filterTrigger = ref<{ $el: HTMLElement } | null>(null)
const overflowTrigger = ref<{ $el: HTMLElement } | null>(null)
const chipClause = computed(() => props.filters.clauses.find(clause => clause.field === chipField.value))
const chipReturnFocus = computed(() => {
  // Reading the partition makes the fallback refresh after responsive overflow changes.
  const isVisible = visible.value.some(chip => chip.field === chipField.value)
  return chipAnchor.value?.isConnected ? chipAnchor.value
    : isVisible ? region.value?.querySelector<HTMLElement>(`[data-chip-field="${chipField.value}"]`) ?? filterTrigger.value?.$el
      : overflowTrigger.value?.$el ?? filterTrigger.value?.$el
})
const editing = ref<BoardFilterField | null>(null)
const editingClause = computed(() => props.filters.clauses.find(clause => clause.field === editing.value))
const region = ref<HTMLElement | null>(null)
const probe = ref<HTMLElement | null>(null)
const visibleCount = ref(0)
const visible = computed(() => chips.value.slice(0, visibleCount.value))
const hidden = computed(() => chips.value.slice(visibleCount.value))
let observer: ResizeObserver | undefined

function measure(): void {
  const available = region.value?.clientWidth ?? 0
  let used = 0
  let count = 0
  for (const chip of Array.from(probe.value?.children ?? [])) {
    used += (chip as HTMLElement).getBoundingClientRect().width + (count ? 6 : 0)
    if (used > available) break
    count += 1
  }
  if (visibleCount.value !== count) visibleCount.value = count
}
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(measure)
    if (region.value) observer.observe(region.value)
    if (probe.value) observer.observe(probe.value)
  }
  measure()
})
function syncChipAnchor(): void {
  if (chipOpen.value && !chips.value.some(chip => chip.field === chipField.value)) chipOpen.value = false
  if (chipOpen.value) {
    const selector = `[data-chip-field="${chipField.value}"]`
    const current = region.value?.querySelector<HTMLElement>(selector)
      ?? (overflowOpen.value ? overflowTrigger.value?.$el.closest('.ui-popover')?.querySelector<HTMLElement>(selector) : null)
      ?? overflowTrigger.value?.$el ?? filterTrigger.value?.$el ?? null
    if (current !== chipAnchor.value) chipAnchor.value = current
  }
}
onUpdated(() => { measure(); syncChipAnchor() })
onBeforeUnmount(() => observer?.disconnect())

function edit(field: ChipField, event: MouseEvent): void {
  filterOpen.value = false
  displayOpen.value = false
  chipField.value = field
  overflowOpen.value = Boolean((event.currentTarget as HTMLElement).closest('.board-filter-bar__chip--overflow'))
  chipAnchor.value = event.currentTarget as HTMLElement
  chipOpen.value = true
}
function remove(field: ChipField): void {
  if (chipField.value === field) chipOpen.value = false
  if (field === 'completed') emit('update:display', { completed: 'all' })
  else emit('removeFilter', field)
}
function apply(clause: BoardFilterClause): void {
  emit('update:filter', clause)
  filterOpen.value = false
  chipOpen.value = false
}
</script>

<template>
  <UiFilterBar
    no-search
    aria-label="Board filters"
    class="board-filter-bar"
  >
    <UiPopover
      v-model="filterOpen"
      aria-label="Filter tasks"
      :width="280"
    >
      <template #trigger="{ toggle, open }">
        <UiButton
          ref="filterTrigger"
          size="sm"
          icon-left="filter"
          data-popover-trigger
          :aria-expanded="open"
          @click="chipOpen = false; displayOpen = false; overflowOpen = false; editing = null; toggle()"
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
          {{ fields.find(field => field.key === editing)?.label ?? 'Contains tickets' }}
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
          class="justify-start"
          @click="editing = field.key"
        >
          {{ field.label }}
        </UiButton>
      </div>
    </UiPopover>
    <div
      ref="region"
      class="board-filter-bar__chips"
      @vue:updated="syncChipAnchor"
    >
      <div
        v-for="chip in visible"
        :key="chip.field"
        class="board-filter-bar__chip"
      >
        <UiButton
          class="board-filter-bar__edit"
          variant="ghost"
          size="sm"
          :title="chip.label"
          :aria-label="`Edit ${chip.label}`"
          :data-chip-field="chip.field"
          @click="edit(chip.field, $event)"
        >
          {{ chip.label }}
        </UiButton>
        <UiIconButton
          class="board-filter-bar__remove"
          size="sm"
          :aria-label="`Remove ${chip.label}`"
          @click="remove(chip.field)"
        >
          <UiIcon
            name="close"
            class="h-3 w-3"
          />
        </UiIconButton>
      </div>
    </div>
    <div class="board-filter-bar__overflow">
      <UiPopover
        v-if="hidden.length"
        v-model="overflowOpen"
        placement="bottom-end"
        aria-label="More filters"
        :width="300"
        :close-on-click-outside="!chipOpen"
        :close-on-esc="!chipOpen"
      >
        <template #trigger="{ toggle, open }">
          <UiButton
            ref="overflowTrigger"
            size="sm"
            variant="ghost"
            data-popover-trigger
            :aria-expanded="open"
            @click="toggle"
          >
            +{{ hidden.length }} filters
          </UiButton>
        </template>
        <div class="grid gap-1 p-1">
          <div
            v-for="chip in hidden"
            :key="chip.field"
            class="board-filter-bar__chip board-filter-bar__chip--overflow"
          >
            <UiButton
              class="board-filter-bar__edit"
              variant="ghost"
              size="sm"
              :title="chip.label"
              :aria-label="`Edit ${chip.label}`"
              :data-chip-field="chip.field"
              @click="edit(chip.field, $event)"
            >
              {{ chip.label }}
            </UiButton>
            <UiIconButton
              class="board-filter-bar__remove"
              size="sm"
              :aria-label="`Remove ${chip.label}`"
              @click="remove(chip.field)"
            >
              <UiIcon
                name="close"
                class="h-3 w-3"
              />
            </UiIconButton>
          </div>
        </div>
      </UiPopover>
    </div>
    <UiPopover
      v-model="chipOpen"
      class="board-filter-bar__editor"
      :anchor="chipAnchor"
      :return-focus="chipReturnFocus"
      aria-label="Edit board filter"
      :width="280"
    >
      <div
        v-if="chipField === 'completed'"
        class="p-3"
      >
        <UiFormField label="Completed tasks">
          <UiSelect
            :model-value="filters.completed"
            :options="completedOptions"
            size="sm"
            @change="$emit('update:display', { completed: $event as BoardFilterState['completed'] }); chipOpen = false"
          />
        </UiFormField>
      </div>
      <BoardFilterEditor
        v-else-if="chipField"
        :key="chipField"
        :field="chipField"
        :clause="chipClause"
        :workflow-options="workflowOptions"
        :assignee-options="assigneeOptions"
        @apply="apply"
      />
    </UiPopover>
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
            @click="chipOpen = false; filterOpen = false; overflowOpen = false; toggle()"
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
              @change="$emit('update:display', { completed: $event as BoardFilterState['completed'] })"
            />
          </UiFormField>
          <label class="flex items-center justify-between gap-3 text-sm text-fg-default">
            Hide empty columns
            <UiSwitch
              size="sm"
              aria-label="Hide empty columns"
              :model-value="filters.hideEmptyColumns"
              @update:model-value="$emit('update:display', { hideEmptyColumns: $event })"
            />
          </label>
        </div>
      </UiPopover>
    </template>
    <div
      ref="probe"
      class="board-filter-bar__probe"
      aria-hidden="true"
      inert
    >
      <span
        v-for="chip in chips"
        :key="chip.field"
        class="board-filter-bar__measure"
      >{{ chip.label }}</span>
    </div>
  </UiFilterBar>
</template>

<style scoped>
.board-filter-bar { position: relative; width: 100%; min-width: 0; padding: 8px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); }
.board-filter-bar :deep(> div:first-child) { flex-wrap: nowrap; min-width: 0; gap: 8px; }
.board-filter-bar :deep(.ui-popover) { flex: none; }
.board-filter-bar :deep(.ui-button) { white-space: nowrap; }
.board-filter-bar__chips { display: flex; flex: 1 1 0; align-items: center; gap: 6px; min-width: 0; }
.board-filter-bar__chip { display: flex; flex: none; align-items: center; max-width: 240px; height: 28px; border: 1px solid var(--color-border-default); border-radius: var(--radius-sm); background: var(--color-bg-surface-alt); }
.board-filter-bar__chip :deep(.board-filter-bar__edit) { min-width: 0; overflow: hidden; text-overflow: ellipsis; display: block; padding: 0 8px; }
.board-filter-bar__chip :deep(.ui-button__label) { display: block; overflow: hidden; text-overflow: ellipsis; }
.board-filter-bar__chip :deep(.board-filter-bar__remove) { flex: none; }
.board-filter-bar__chip--overflow { width: 100%; max-width: 100%; }
.board-filter-bar__chip--overflow :deep(.board-filter-bar__edit) { flex: 1; text-align: left; }
.board-filter-bar__overflow { flex: 0 0 92px; }
.board-filter-bar__editor { position: absolute; }
.board-filter-bar__count { display: inline-block; min-width: 90px; text-align: center; white-space: nowrap; font-variant-numeric: tabular-nums; }
.board-filter-bar__probe { position: absolute; visibility: hidden; pointer-events: none; display: flex; gap: 6px; height: 0; overflow: hidden; max-width: 100%; }
.board-filter-bar__measure { display: inline-block; flex: none; max-width: 240px; padding: 0 44px 0 8px; border: 1px solid transparent; font-size: 12px; font-weight: var(--fw-medium); white-space: nowrap; }
</style>
