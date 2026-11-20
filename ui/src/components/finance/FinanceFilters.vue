<script setup lang="ts">
import { computed, onBeforeUpdate, ref } from 'vue'
import { UiButton, UiFilterBar, UiFormField, UiInput, UiPopover, UiSelect } from '@/components/ui'
import { comparisonWindow, timeframe, type FinanceQuery } from '@/lib/finance'
import FinanceFilterEditor, {
  type FinanceFilterField,
  type FinanceFilterOptions,
} from './FinanceFilterEditor.vue'

const props = withDefaults(
  defineProps<{
    query: FinanceQuery
    disabled?: boolean
    showSearch?: boolean
    filterOptions?: FinanceFilterOptions | null
  }>(),
  { showSearch: true, filterOptions: null },
)
const emit = defineEmits<{ (e: 'apply', query: FinanceQuery): void }>()
const bar = ref<InstanceType<typeof UiFilterBar> | null>(null)
const periodOpen = ref(false),
  filterOpen = ref(false),
  reportOpen = ref(false)
const editing = ref<FinanceFilterField | null>(null)
const editorVersion = ref(0)
const search = ref(props.query.q || '')
const dates = ref({ start: props.query.start, end: props.query.end })
const compare = ref(comparisonMode(props.query))
const periodError = ref('')
const periods = [
  { value: 'year', label: 'This year' },
  { value: 'week', label: 'This week' },
  { value: 'month', label: 'This month' },
  { value: 'quarter', label: 'This quarter' },
  { value: 'last-month', label: 'Last month' },
  { value: 'year-to-date', label: 'Year to date' },
  { value: 'all', label: 'All time' },
  { value: 'custom', label: 'Custom dates' },
]
const fields = computed<{ key: FinanceFilterField; label: string }[]>(() => [
  { key: 'kind', label: 'Record type' },
  { key: 'currency', label: 'Currency' },
  { key: 'needs_review', label: 'Needs review' },
  { key: 'q', label: 'Find records' },
  ...(props.filterOptions?.accounts.some((option) => option.label?.trim())
    ? [{ key: 'account_ref' as const, label: 'Account' }]
    : []),
  ...(props.filterOptions?.counterparties.some((option) => option.label?.trim())
    ? [{ key: 'counterparty_ref' as const, label: 'Counterparty' }]
    : []),
])
const kindLabels: Record<string, string> = {
  expense: 'Expenses',
  income: 'Income',
  bill: 'Bills',
  receipt: 'Receipts',
}
const chips = computed(() => [
  ...(props.query.q ? [{ key: 'q', label: `Search: ${props.query.q}` }] : []),
  ...(props.query.kind && props.query.kind !== 'all'
    ? [{ key: 'kind', label: `Type: ${kindLabels[props.query.kind] || props.query.kind}` }]
    : []),
  ...(props.query.currency
    ? [{ key: 'currency', label: `Currency: ${props.query.currency}` }]
    : []),
  ...(props.query.needs_review ? [{ key: 'needs_review', label: 'Needs review' }] : []),
  ...(['account_ref', 'category_ref', 'counterparty_ref', 'external_project_ref'] as const)
    .filter((key) => props.query[key])
    .map((key) => ({ key, label: referenceLabel(key) })),
])
function referenceLabel(
  key: 'account_ref' | 'category_ref' | 'counterparty_ref' | 'external_project_ref',
): string {
  const label =
    key === 'account_ref'
      ? props.filterOptions?.accounts.find(
          (option) =>
            option.value === props.query[key] || option.aliases.includes(props.query[key] || ''),
        )?.label
      : key === 'counterparty_ref'
        ? props.filterOptions?.counterparties.find((option) => option.value === props.query[key])
            ?.label
        : null
  return label?.trim()
    ? `${key === 'account_ref' ? 'Account' : 'Counterparty'}: ${label}`
    : {
        account_ref: 'Selected account',
        category_ref: 'Selected category',
        counterparty_ref: 'Selected counterparty',
        external_project_ref: 'Selected external project',
      }[key]
}
function presetFor(range: { start: string; end: string }): string {
  return (
    periods.find(
      (period) =>
        period.value !== 'custom' &&
        Object.entries(timeframe(period.value)).every(
          ([key, value]) => range[key as keyof typeof range] === value,
        ),
    )?.value || 'custom'
  )
}
const preset = computed(() => presetFor(dates.value))
const periodLabel = computed(() => {
  const selected = presetFor(props.query)
  if (selected !== 'custom') return periods.find((period) => period.value === selected)!.label
  const format = (date: string) => {
    const parsed = new Date(`${date}T12:00:00Z`)
    return Number.isFinite(parsed.getTime())
      ? new Intl.DateTimeFormat('en', {
          month: 'short',
          day: 'numeric',
          year: 'numeric',
          timeZone: 'UTC',
        }).format(parsed)
      : 'Choose dates'
  }
  return `${format(props.query.start)} – ${format(props.query.end)}`
})
function comparisonMode(query: FinanceQuery): string {
  if (!query.comparison) return 'none'
  for (const mode of ['period', 'year']) {
    try {
      const range = comparisonWindow(query.start, query.end, mode)
      if (range?.start === query.comparison.start && range?.end === query.comparison.end)
        return mode
    } catch {
      /* Invalid incoming dates remain correctable in the period editor. */
    }
  }
  return 'none'
}
function closeOthers(): void {
  bar.value?.closeEditor()
  periodOpen.value = false
  filterOpen.value = false
  reportOpen.value = false
}
function openPeriod(): void {
  closeOthers()
  dates.value = { start: props.query.start, end: props.query.end }
  compare.value = comparisonMode(props.query)
  periodError.value = ''
  periodOpen.value = true
}
function setPreset(value: string | number | null): void {
  if (value === 'custom') return
  dates.value = timeframe(String(value))
  if (value === 'all') compare.value = 'none'
}
function applyPeriod(): void {
  if (props.disabled) return
  periodError.value = ''
  try {
    for (const value of [dates.value.start, dates.value.end]) {
      const date = new Date(`${value}T12:00:00Z`)
      if (
        !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
        value < '0001-01-01' ||
        value > '9999-12-31' ||
        !Number.isFinite(date.getTime()) ||
        date.toISOString().slice(0, 10) !== value
      )
        throw new Error('Choose valid dates.')
    }
    if (dates.value.start > dates.value.end) throw new Error('Start must precede end.')
    applyChanges({
      ...dates.value,
      comparison:
        preset.value === 'all'
          ? null
          : comparisonWindow(dates.value.start, dates.value.end, compare.value),
    })
  } catch (error) {
    periodError.value = error instanceof Error ? error.message : 'Choose valid dates.'
  }
}
function applyChanges(changes: Partial<FinanceQuery>): void {
  if (props.disabled) return
  emit('apply', { ...props.query, ...changes, offset: 0 })
  closeOthers()
}
function startEditor(key: string): void {
  periodOpen.value = false
  filterOpen.value = false
  reportOpen.value = false
  editing.value = key as FinanceFilterField
  editorVersion.value += 1
}
function toggleFilter(open: boolean, setOpen: (value: boolean) => void): void {
  closeOthers()
  editing.value = null
  setOpen(!open)
}
function selectField(field: FinanceFilterField): void {
  editing.value = field
  editorVersion.value += 1
}
function toggleReport(open: boolean, setOpen: (value: boolean) => void): void {
  closeOthers()
  editorVersion.value += 1
  setOpen(!open)
}
function remove(key: string): void {
  if (key === 'kind') applyChanges({ kind: 'all' as FinanceQuery['kind'] })
  else if (key === 'needs_review') applyChanges({ needs_review: false })
  else if (key === 'external_project_ref')
    applyChanges({ external_project_ref: null, scope: 'business' as FinanceQuery['scope'] })
  else applyChanges({ [key]: null })
}
function clear(): void {
  // Clearing population filters preserves the chosen dates, comparison and reporting options.
  applyChanges({
    q: null,
    kind: 'all' as FinanceQuery['kind'],
    currency: null,
    needs_review: false,
    account_ref: null,
    category_ref: null,
    counterparty_ref: null,
    external_project_ref: null,
    scope: 'business' as FinanceQuery['scope'],
  })
  search.value = ''
}
let appliedQuery = JSON.stringify(props.query)
onBeforeUpdate(() => {
  const next = JSON.stringify(props.query)
  if (next !== appliedQuery) {
    appliedQuery = next
    search.value = props.query.q || ''
    closeOthers()
  }
})
</script>

<template>
  <div class="space-y-2">
    <UiFilterBar
      ref="bar"
      no-search
      class="finance-filter-bar"
      aria-label="Finance filters"
      :editable-active="chips"
      chip-editor-label="Edit Finance filter"
      :disabled="disabled"
      @edit="startEditor"
      @remove="remove"
    >
      <UiPopover
        v-model="periodOpen"
        aria-label="Choose period"
        :width="320"
      >
        <template #trigger="{ open, setOpen }">
          <UiButton
            size="sm"
            icon-left="calendar"
            icon-right="chevron-down"
            class="finance-filter-bar__period"
            :title="periodLabel"
            :disabled="disabled"
            :aria-expanded="open"
            data-popover-trigger
            data-test="finance-period"
            @click="open ? setOpen(false) : openPeriod()"
          >
            {{ periodLabel }}
          </UiButton>
        </template>
        <form
          class="space-y-3 p-3"
          aria-label="Finance period"
          @submit.prevent="applyPeriod"
        >
          <UiFormField label="Period">
            <UiSelect
              :model-value="preset"
              :options="periods"
              size="sm"
              :disabled="disabled"
              @update:model-value="setPreset"
            />
          </UiFormField>
          <div class="grid grid-cols-2 gap-3">
            <UiFormField
              label="From"
              :error="periodError"
            >
              <UiInput
                v-model="dates.start"
                type="date"
                size="sm"
                min="0001-01-01"
                max="9999-12-31"
                :disabled="disabled"
                data-test="filter-start"
              />
            </UiFormField>
            <UiFormField label="Through">
              <UiInput
                v-model="dates.end"
                type="date"
                size="sm"
                min="0001-01-01"
                max="9999-12-31"
                :disabled="disabled"
                data-test="filter-end"
              />
            </UiFormField>
          </div>
          <UiFormField label="Compare with">
            <UiSelect
              v-model="compare"
              :disabled="disabled || preset === 'all'"
              :options="[
                { value: 'none', label: 'No comparison' },
                { value: 'period', label: 'Previous period' },
                { value: 'year', label: 'Previous year' },
              ]"
              size="sm"
            />
          </UiFormField>
          <UiButton
            type="submit"
            size="sm"
            :disabled="disabled"
            data-test="apply-finance-period"
          >
            Apply period
          </UiButton>
        </form>
      </UiPopover>
      <UiPopover
        v-model="filterOpen"
        aria-label="Filter Finance records"
        :width="280"
      >
        <template #trigger="{ open, setOpen }">
          <UiButton
            size="sm"
            icon-left="filter"
            :disabled="disabled"
            :aria-expanded="open"
            data-popover-trigger
            @click="toggleFilter(open, setOpen)"
          >
            Filter
          </UiButton>
        </template>
        <template v-if="editing">
          <UiButton
            size="sm"
            variant="ghost"
            icon-left="chevron-left"
            @click="editing = null"
          >
            Filter by
          </UiButton>
          <FinanceFilterEditor
            :key="`${editing}-${editorVersion}`"
            :field="editing"
            :query="query"
            :disabled="disabled"
            :filter-options="filterOptions"
            @apply="applyChanges"
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
            @click="selectField(field.key)"
          >
            {{ field.label }}
          </UiButton>
        </div>
      </UiPopover>
      <template #editor>
        <FinanceFilterEditor
          v-if="editing"
          :key="`${editing}-${editorVersion}`"
          :field="editing"
          :query="query"
          :disabled="disabled"
          :filter-options="filterOptions"
          @apply="applyChanges"
        />
      </template>
      <template #right>
        <UiButton
          variant="ghost"
          size="sm"
          :disabled="disabled || !chips.length"
          title="Clear filters; keep period and report options"
          @click="clear"
        >
          Clear filters
        </UiButton>
        <UiPopover
          v-model="reportOpen"
          placement="bottom-end"
          aria-label="Report options"
          :width="300"
        >
          <template #trigger="{ open, setOpen }">
            <UiButton
              size="sm"
              variant="ghost"
              icon-right="chevron-down"
              :disabled="disabled"
              :aria-expanded="open"
              data-popover-trigger
              @click="toggleReport(open, setOpen)"
            >
              Report options
            </UiButton>
          </template>
          <FinanceFilterEditor
            :key="editorVersion"
            field="report"
            :query="query"
            :disabled="disabled"
            @apply="applyChanges"
          />
        </UiPopover>
      </template>
    </UiFilterBar>
    <form
      v-if="showSearch"
      class="flex items-center gap-2"
      aria-label="Search Finance records"
      @submit.prevent="applyChanges({ q: search.trim() || null })"
    >
      <UiFormField
        label="Find records"
        hide-label
        class="w-72"
      >
        <UiInput
          v-model="search"
          type="search"
          size="sm"
          placeholder="Find invoice, vendor or source"
          :disabled="disabled"
          data-test="finance-search"
        />
      </UiFormField>
      <UiButton
        type="submit"
        size="sm"
        :disabled="disabled"
      >
        Search
      </UiButton>
    </form>
  </div>
</template>

<style scoped>
.finance-filter-bar {
  padding: 8px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-lg);
  background: var(--color-bg-surface);
  min-width: 0;
}
.finance-filter-bar__period {
  max-width: 240px;
}
.finance-filter-bar__period :deep(.ui-button__label) {
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
