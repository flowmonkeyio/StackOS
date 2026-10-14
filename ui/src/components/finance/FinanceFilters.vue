<script setup lang="ts">
import { ref, watch } from 'vue'
import { UiButton, UiCheckbox, UiFormField, UiInput, UiSelect } from '@/components/ui'
import { comparisonWindow, timeframe, type FinanceQuery } from '@/lib/finance'
const props = defineProps<{ query: FinanceQuery; disabled?: boolean }>()
const emit = defineEmits<{ (e: 'apply', query: FinanceQuery): void }>()
const draft = ref<FinanceQuery>({ ...props.query })
const preset = ref('month'),
  compare = ref('none'),
  error = ref('')
watch(
  () => props.query,
  (value) => {
    draft.value = { ...value }
  },
  { deep: true },
)
function setPreset(value: string | number | null) {
  preset.value = String(value)
  if (preset.value !== 'custom') Object.assign(draft.value, timeframe(preset.value))
  if (preset.value === 'all') compare.value = 'none'
}
function apply() {
  error.value = ''
  try {
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
    <div class="grid grid-cols-4 gap-3">
      <UiFormField label="Timeframe"
        ><UiSelect
          :model-value="preset"
          :options="[
            { value: 'month', label: 'This month' },
            { value: 'last-month', label: 'Last month' },
            { value: 'year', label: 'Year to date' },
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
          @update:model-value="preset = 'custom'"
      /></UiFormField>
      <UiFormField label="Through"
        ><UiInput
          v-model="draft.end"
          type="date"
          min="0001-01-01"
          max="9999-12-31"
          data-test="filter-end"
          @update:model-value="preset = 'custom'"
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
    <details class="text-sm">
      <summary class="focus-ring cursor-pointer">
        Account, category, currency and project filters
      </summary>
      <div class="mt-3 grid grid-cols-4 gap-3">
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
    </details>
    <p v-if="error" role="alert" class="text-sm text-danger-fg">{{ error }}</p>
    <UiButton type="submit" :disabled="disabled" data-test="apply-finance-filters"
      >Apply timeframe and filters</UiButton
    >
  </form>
</template>
