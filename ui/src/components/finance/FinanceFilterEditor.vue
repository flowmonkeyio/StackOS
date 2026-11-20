<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiButton, UiCheckbox, UiFormField, UiInput, UiSelect } from '@/components/ui'
import type { SchemaFinanceFilterOptionsOut } from '@/api'
import type { FinanceQuery } from '@/lib/finance'

export type FinanceFilterField =
  | 'kind'
  | 'currency'
  | 'needs_review'
  | 'q'
  | 'account_ref'
  | 'category_ref'
  | 'counterparty_ref'
  | 'external_project_ref'
  | 'report'
export type FinanceFilterOptions = SchemaFinanceFilterOptionsOut
const props = defineProps<{
  field: FinanceFilterField
  query: FinanceQuery
  disabled?: boolean
  filterOptions?: FinanceFilterOptions | null
}>()
const emit = defineEmits<{ (e: 'apply', changes: Partial<FinanceQuery>): void }>()
const draft = ref({ ...props.query })
const error = ref('')
const referenceLabels: Record<string, string> = {
  account_ref: 'Account',
  category_ref: 'Category',
  counterparty_ref: 'Counterparty',
  external_project_ref: 'External project',
}
const choices = computed(
  () =>
    (props.field === 'account_ref'
      ? props.filterOptions?.accounts
      : props.field === 'counterparty_ref'
        ? props.filterOptions?.counterparties
        : []
    )
      ?.filter((option) => option.label?.trim())
      .map((option) => ({ value: option.value, label: option.label! })) || [],
)
const selectedReference =
  props.field === 'account_ref'
    ? props.filterOptions?.accounts.find(
        (option) =>
          option.value === props.query.account_ref ||
          option.aliases.includes(props.query.account_ref || ''),
      )?.value
    : props.field === 'counterparty_ref'
      ? props.query.counterparty_ref
      : null
const referenceValue = ref<string | null>(
  selectedReference && choices.value.some((option) => option.value === selectedReference)
    ? selectedReference
    : props.query[props.field as keyof FinanceQuery]
      ? null
      : '',
)
const kindOptions = [
  { value: 'all', label: 'All records' },
  { value: 'expense', label: 'Expenses' },
  { value: 'income', label: 'Income' },
  { value: 'bill', label: 'Bills' },
  { value: 'receipt', label: 'Receipts' },
]
function apply(): void {
  if (props.disabled) return
  error.value = ''
  if (props.field === 'report') {
    const timezone = draft.value.timezone.trim()
    try {
      new Intl.DateTimeFormat('en', { timeZone: timezone }).format()
    } catch {
      error.value = 'Enter a valid timezone, such as America/New_York or UTC.'
      return
    }
    emit('apply', {
      recognition_basis: draft.value.recognition_basis,
      date_basis: draft.value.date_basis,
      timezone,
    })
  } else if (props.field === 'currency') {
    const currency = draft.value.currency?.trim().toUpperCase() || ''
    if (currency && !/^[A-Z]{3}$/.test(currency)) {
      error.value = 'Use a three-letter currency code, such as USD.'
      return
    }
    emit('apply', { currency: currency || null })
  } else if (props.field === 'q') emit('apply', { q: draft.value.q?.trim() || null })
  else if (props.field === 'needs_review')
    emit('apply', { needs_review: Boolean(draft.value.needs_review) })
  else if (props.field === 'kind') emit('apply', { kind: draft.value.kind })
  else if (choices.value.length && referenceValue.value !== null)
    emit('apply', { [props.field]: referenceValue.value || null })
}
</script>

<template>
  <form
    class="space-y-3 p-3"
    aria-label="Finance filter editor"
    @submit.prevent="apply"
  >
    <UiFormField
      v-if="field === 'kind'"
      label="Record type"
    >
      <UiSelect
        v-model="draft.kind"
        :options="kindOptions"
        size="sm"
        :disabled="disabled"
      />
    </UiFormField>
    <UiFormField
      v-else-if="field === 'currency'"
      label="Currency"
      :error="error"
      help="Three-letter code, such as USD."
    >
      <UiInput
        v-model="draft.currency"
        size="sm"
        placeholder="All currencies"
        maxlength="3"
        :disabled="disabled"
        data-test="filter-currency"
      />
    </UiFormField>
    <UiFormField
      v-else-if="field === 'q'"
      label="Find records"
    >
      <UiInput
        v-model="draft.q"
        type="search"
        size="sm"
        :disabled="disabled"
      />
    </UiFormField>
    <UiCheckbox
      v-else-if="field === 'needs_review'"
      v-model="draft.needs_review"
      label="Needs review only"
      :disabled="disabled"
    />
    <template v-else-if="field === 'report'">
      <UiFormField label="Recognize income and expenses">
        <UiSelect
          v-model="draft.recognition_basis"
          size="sm"
          :options="[
            { value: 'cash', label: 'When money moves' },
            { value: 'accrual', label: 'When earned or incurred' },
          ]"
          :disabled="disabled"
        />
      </UiFormField>
      <UiFormField label="Use dates from">
        <UiSelect
          v-model="draft.date_basis"
          size="sm"
          :options="[
            { value: 'transaction', label: 'Transactions' },
            { value: 'posting', label: 'Posted entries' },
            { value: 'issue', label: 'Issued documents' },
            { value: 'cash', label: 'Payments' },
          ]"
          :disabled="disabled"
        />
      </UiFormField>
      <UiFormField
        label="Timezone"
        :error="error"
      >
        <UiInput
          v-model="draft.timezone"
          size="sm"
          :disabled="disabled"
          data-test="filter-timezone"
        />
      </UiFormField>
    </template>
    <UiFormField
      v-else-if="choices.length"
      :label="referenceLabels[field]"
    >
      <UiSelect
        v-model="referenceValue"
        size="sm"
        :options="[
          { value: '', label: field === 'account_ref' ? 'All accounts' : 'All counterparties' },
          ...choices,
        ]"
        :placeholder="query[field] ? 'Replace selected filter' : 'Choose a filter'"
        :disabled="disabled"
      />
      <details
        v-if="query[field]"
        class="mt-2 text-xs text-fg-muted"
      >
        <summary class="cursor-pointer rounded-sm focus-ring">
          Inspect current selection
        </summary>
        <code class="mt-2 block break-all">{{ query[field] }}</code>
      </details>
    </UiFormField>
    <template v-else>
      <p class="text-sm font-medium text-fg-strong">
        Selected {{ referenceLabels[field]?.toLowerCase() }}
      </p>
      <p class="text-xs text-fg-muted">
        This selection is active. Remove this filter to include every
        {{ referenceLabels[field]?.toLowerCase() }}.
      </p>
      <details class="text-xs text-fg-muted">
        <summary class="cursor-pointer rounded-sm focus-ring">
          Inspect selection
        </summary>
        <code class="mt-2 block break-all">{{ query[field] }}</code>
      </details>
    </template>
    <UiButton
      v-if="!referenceLabels[field] || choices.length"
      type="submit"
      size="sm"
      :disabled="disabled || (Boolean(referenceLabels[field]) && referenceValue === null)"
      data-test="apply-finance-filter"
    >
      Apply
    </UiButton>
  </form>
</template>
