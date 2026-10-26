<script setup lang="ts">
import { onBeforeUpdate, ref, useId } from 'vue'
import {
  UiButton,
  UiButtonGroup,
  UiCheckbox,
  UiFormField,
  UiInput,
  UiSelect,
} from '@/components/ui'
import { comparisonWindow, timeframe, type FinanceQuery } from '@/lib/finance'
const props = defineProps<{ query: FinanceQuery; disabled?: boolean }>()
const emit = defineEmits<{ (e: 'apply', query: FinanceQuery): void }>()
const draft = ref<FinanceQuery>({ ...props.query })
const expanded = ref(false)
const controlsId = `finance-filter-controls-${useId()}`
const periods = [
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Quarter' },
  { value: 'year', label: 'Year' },
]
function matchesPreset(value: string) {
  const range = timeframe(value)
  return draft.value.start === range.start && draft.value.end === range.end
}
function currentPreset() {
  return (
    ['year', 'week', 'month', 'quarter', 'year-to-date', 'last-month', 'all'].find(matchesPreset) ||
    'custom'
  )
}
function comparisonMode(query: FinanceQuery, preferred = 'none') {
  if (!query.comparison || (query.start === '0001-01-01' && query.end === '9999-12-31'))
    return 'none'
  return (
    [preferred, 'period', 'year'].find((mode) => {
      if (mode === 'none') return false
      const range = comparisonWindow(query.start, query.end, mode)
      return range?.start === query.comparison?.start && range?.end === query.comparison?.end
    }) || 'none'
  )
}
const preset = ref(currentPreset()),
  compare = ref(comparisonMode(props.query)),
  error = ref('')
let appliedQuery = JSON.stringify(props.query)
onBeforeUpdate(() => {
  const nextQuery = JSON.stringify(props.query)
  if (nextQuery !== appliedQuery) {
    appliedQuery = nextQuery
    draft.value = { ...props.query }
    preset.value = currentPreset()
    compare.value = comparisonMode(props.query, compare.value)
  }
})
function setPreset(value: string | number | null) {
  preset.value = String(value)
  if (preset.value !== 'custom') Object.assign(draft.value, timeframe(preset.value))
  if (preset.value === 'all') compare.value = 'none'
}
function shortcut(value: string) {
  if (props.disabled) return
  setPreset(value)
  apply()
}
function apply() {
  if (props.disabled) return
  error.value = ''
  try {
    if (!draft.value.start || !draft.value.end) throw new Error('Choose valid dates.')
    if (draft.value.start > draft.value.end) throw new Error('Start must precede end.')
    emit('apply', {
      ...draft.value,
      comparison:
        preset.value === 'all'
          ? null
          : comparisonWindow(draft.value.start, draft.value.end, compare.value),
      offset: 0,
    })
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Check the selected dates.'
  }
}
</script>
<template>
  <form
    class="space-y-3 rounded-md border border-default bg-bg-surface p-3"
    aria-label="Finance timeframe and filters"
    @submit.prevent="apply"
  >
    <div class="flex flex-wrap items-center gap-3">
      <UiButtonGroup aria-label="Current calendar period" :attached="false" class="flex-wrap">
        <UiButton
          v-for="period in periods"
          :key="period.value"
          type="button"
          size="sm"
          :variant="matchesPreset(period.value) ? 'primary' : 'secondary'"
          :aria-pressed="matchesPreset(period.value)"
          :disabled="disabled"
          :data-test="`finance-period-${period.value}`"
          @click="shortcut(period.value)"
          >{{ period.label }}</UiButton
        >
      </UiButtonGroup>
      <span class="text-sm text-fg-muted">Calendar shortcuts use UTC dates.</span>
      <UiButton
        type="button"
        size="sm"
        class="ml-auto"
        :icon-right="expanded ? 'chevron-up' : 'chevron-down'"
        :aria-expanded="expanded"
        :aria-controls="controlsId"
        data-test="toggle-finance-filters"
        @click="expanded = !expanded"
        >{{ expanded ? 'Hide filters' : 'Show filters' }}</UiButton
      >
    </div>
    <div
      v-show="expanded"
      :id="controlsId"
      role="region"
      aria-label="Finance filter controls"
      class="space-y-3"
      data-test="finance-filter-controls"
    >
      <div class="grid grid-cols-4 gap-3">
        <UiFormField label="Timeframe"
          ><UiSelect
            :model-value="preset"
            :options="[
              { value: 'year', label: 'Calendar year' },
              { value: 'week', label: 'This week' },
              { value: 'month', label: 'This month' },
              { value: 'quarter', label: 'This quarter' },
              { value: 'last-month', label: 'Last month' },
              { value: 'year-to-date', label: 'Year to date' },
              { value: 'all', label: 'All time' },
              { value: 'custom', label: 'Custom dates' },
            ]"
            @update:model-value="setPreset"
        /></UiFormField>
        <UiFormField label="From"
          ><UiInput
            v-model="draft.start"
            type="date"
            min="0001-01-01"
            max="9999-12-31"
            data-test="filter-start"
            @update:model-value="preset = currentPreset()"
        /></UiFormField>
        <UiFormField label="Through"
          ><UiInput
            v-model="draft.end"
            type="date"
            min="0001-01-01"
            max="9999-12-31"
            data-test="filter-end"
            @update:model-value="preset = currentPreset()"
        /></UiFormField>
        <UiFormField
          label="Compare with"
          :help="
            preset === 'all'
              ? 'All-time comparison is unavailable; choose a bounded period.'
              : 'Same selected population and basis.'
          "
          ><UiSelect
            v-model="compare"
            :disabled="preset === 'all'"
            :options="[
              { value: 'none', label: 'No comparison' },
              { value: 'period', label: 'Previous period' },
              { value: 'year', label: 'Previous year' },
            ]"
        /></UiFormField>
        <UiFormField
          label="Find invoice or record"
          help="Search stays inside the selected timeframe. Use All time for old invoices."
          ><UiInput
            v-model="draft.q"
            type="search"
            data-test="finance-search"
            placeholder="Invoice ID, vendor or source"
        /></UiFormField>
        <UiFormField label="Record type"
          ><UiSelect
            v-model="draft.kind"
            :options="[
              { value: 'all', label: 'All records' },
              { value: 'expense', label: 'Expenses' },
              { value: 'income', label: 'Income' },
              { value: 'bill', label: 'Bills' },
              { value: 'receipt', label: 'Receipts' },
            ]"
        /></UiFormField>
        <UiFormField label="Recognition basis"
          ><UiSelect v-model="draft.recognition_basis" :options="['cash', 'accrual']"
        /></UiFormField>
        <UiFormField label="Date basis"
          ><UiSelect
            v-model="draft.date_basis"
            :options="['transaction', 'posting', 'issue', 'cash']"
        /></UiFormField>
      </div>
      <div class="grid grid-cols-4 gap-3">
        <UiFormField label="Account reference"><UiInput v-model="draft.account_ref" /></UiFormField>
        <UiFormField label="Category reference"
          ><UiInput v-model="draft.category_ref"
        /></UiFormField>
        <UiFormField label="Counterparty reference"
          ><UiInput v-model="draft.counterparty_ref"
        /></UiFormField>
        <UiFormField label="Currency"
          ><UiInput v-model="draft.currency" placeholder="All currencies" maxlength="3"
        /></UiFormField>
        <UiFormField label="Business or project"
          ><UiSelect
            v-model="draft.scope"
            :options="[
              { value: 'business', label: 'Whole business' },
              { value: 'external-project', label: 'One external project' },
            ]"
        /></UiFormField>
        <UiFormField v-if="draft.scope === 'external-project'" label="External project reference"
          ><UiInput v-model="draft.external_project_ref"
        /></UiFormField>
        <UiFormField label="Timezone"><UiInput v-model="draft.timezone" /></UiFormField>
        <UiCheckbox v-model="draft.needs_review" label="Needs review only" />
      </div>
      <p v-if="error" role="alert" class="text-sm text-danger-fg">{{ error }}</p>
      <UiButton type="submit" :disabled="disabled" data-test="apply-finance-filters"
        >Apply timeframe and filters</UiButton
      >
    </div>
  </form>
</template>
