<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiButton, UiCheckbox, UiFormField, UiInput, UiSelect } from '@/components/ui'
import { trackerStatus } from '@/design/status'
import type { TrackerStatus } from '@/lib/task-tracker/types'
import type { TrackerSelectOption } from './viewTypes'
import type { BoardFilterClause, BoardFilterField, FilterOperator, TaskPriority } from './boardFilters'

const props = defineProps<{
  field: BoardFilterField
  clause?: BoardFilterClause
  workflowOptions: TrackerSelectOption[]
  assigneeOptions: TrackerSelectOption[]
}>()
const emit = defineEmits<{ (e: 'apply', clause: BoardFilterClause): void }>()
const value = ref(props.clause && 'value' in props.clause ? props.clause.value : '')
const values = ref<string[]>(props.clause && 'values' in props.clause ? [...props.clause.values] : [])
const operator = ref<FilterOperator>(props.clause && 'operator' in props.clause ? props.clause.operator : 'is')
const operators = [{ value: 'is', label: 'is' }, { value: 'is-not', label: 'is not' }]
const statuses = Object.entries(trackerStatus).map(([value, status]) => ({ value, label: status.label }))
const priorities = ['p0', 'p1', 'p2', 'p3'].map(value => ({ value, label: value.toUpperCase() }))
const options = computed(() => props.field === 'workflow' ? props.workflowOptions : props.assigneeOptions)
const singleOptions = computed(() => {
  const choices = options.value.filter(option => option.value !== '')
  // Keep an applied URL value editable even if it disappeared from the current index.
  return value.value && !choices.some(option => option.value === value.value)
    ? [{ value: value.value, label: value.value }, ...choices] : choices
})
function toggle(value: string, checked: boolean): void {
  values.value = checked ? [...values.value, value] : values.value.filter(item => item !== value)
}
function apply(): void {
  switch (props.field) {
    case 'text': emit('apply', { field: 'text', value: value.value }); break
    case 'status': emit('apply', { field: 'status', operator: operator.value, values: values.value as TrackerStatus[] }); break
    case 'priority': emit('apply', { field: 'priority', operator: operator.value, values: values.value as TaskPriority[] }); break
    case 'workflow': case 'assignee': emit('apply', { field: props.field, operator: operator.value, value: value.value }); break
    case 'ticket-status': emit('apply', { field: 'ticket-status', value: value.value as TrackerStatus }); break
    case 'blocked': emit('apply', { field: 'blocked' }); break
  }
}
</script>

<template>
  <form
    class="space-y-3 p-2"
    @submit.prevent="apply"
  >
    <UiSelect
      v-if="['status', 'priority', 'workflow', 'assignee'].includes(field)"
      v-model="operator"
      :options="operators"
      aria-label="Filter operator"
      size="sm"
    />
    <UiFormField
      v-if="field === 'text'"
      label="Text contains"
    >
      <UiInput
        v-model="value"
        type="search"
        placeholder="Search tasks"
      />
    </UiFormField>
    <div
      v-else-if="field === 'status' || field === 'priority'"
      class="grid gap-3"
      :aria-label="`${field} values`"
    >
      <UiCheckbox
        v-for="option in field === 'status' ? statuses : priorities"
        :key="option.value"
        :label="option.label"
        :model-value="values.includes(option.value)"
        @update:model-value="toggle(option.value, $event)"
      />
    </div>
    <UiSelect
      v-else-if="field === 'workflow' || field === 'assignee'"
      v-model="value"
      :options="singleOptions"
      :aria-label="field === 'workflow' ? 'Workflow value' : 'Assignee value'"
      searchable
      empty-label="No values available"
    />
    <UiSelect
      v-else-if="field === 'ticket-status'"
      v-model="value"
      :options="statuses"
      aria-label="Ticket status value"
    />
    <p
      v-else
      class="text-sm text-fg-muted"
    >
      Tasks containing blocked tickets.
    </p>
    <div class="flex justify-end">
      <UiButton
        type="submit"
        size="sm"
        variant="primary"
      >
        Apply
      </UiButton>
    </div>
  </form>
</template>
