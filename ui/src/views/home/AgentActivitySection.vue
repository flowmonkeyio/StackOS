<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiButton, UiButtonGroup, UiCallout, UiFormField, UiInput, UiSelect, UiSkeleton } from '@/components/ui'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import AgentActivityChart from './AgentActivityChart.vue'
import DeliveryTrendChart from './DeliveryTrendChart.vue'
import { deliveryMetrics } from '@/lib/deliveryTrends'
import { exactUsage, formatTokens, groupLabel, usageValue, type AgentStats, type AgentStatsFilters, type ActivityGroup, type UsageField } from '@/lib/agentActivity'

const props = defineProps<{ summary: AgentStats | null; loading: boolean; error: string | null; filters: AgentStatsFilters; global?: boolean }>()
const emit = defineEmits<{ filters: [value: Partial<AgentStatsFilters>] }>()
const tokenField = ref<UsageField>('total')
const groups = [{ value: 'agent', label: 'Agent' }, { value: 'model', label: 'Model' }, { value: 'agent_model', label: 'Agent + model' }] as const
const displayGroup = computed(() => props.summary?.group_by ?? props.filters.group_by)
const palette = ['info-default', 'chart-work', 'warning-default', 'accent-primary', 'success-default', 'fg-subtle']
const rows = computed(() => {
  const source = props.summary?.rows ?? []
  const keys = new Map(source.map(row => row.group_key).sort().map((key, index) => [key, index]))
  const max = Math.max(1, ...source.map(row => usageValue(row, 'total') ?? 0))
  return source.map(row => ({ ...row, label: groupLabel(row, displayGroup.value, props.global),
    token: palette[keys.get(row.group_key)! % palette.length], value: usageValue(row, 'total'),
    width: (usageValue(row, 'total') ?? 0) / max * 100,
  }))
})
const presets = [{ value: '7', label: 'Last 7 days' }, { value: '30', label: 'Last 30 days' }, { value: '90', label: 'Last 90 days' }, { value: '365', label: 'Last 365 days' }, { value: 'custom', label: 'Custom range' }]
const period = computed(() => {
  const length = (Date.parse(props.filters.end_date) - Date.parse(props.filters.start_date)) / 86_400_000 + 1
  return presets.some(item => item.value === String(length)) ? String(length) : 'custom'
})
function setPeriod(value: string | number | null): void {
  if (!value || value === 'custom') return
  const end = new Date(`${props.filters.end_date}T12:00:00Z`)
  end.setUTCDate(end.getUTCDate() - Number(value) + 1)
  emit('filters', { start_date: end.toISOString().slice(0, 10) })
}
function setDate(field: 'start_date' | 'end_date', value: string | number | null): void { emit('filters', { [field]: String(value ?? '') }) }
</script>

<template>
  <section
    aria-label="Agent activity"
    :aria-busy="loading"
    class="agent-activity"
  >
    <div class="agent-activity__header">
      <div class="agent-activity__heading">
        <h2 class="text-2xl font-semibold text-fg-strong">
          Usage
        </h2>
        <p class="mt-1 text-xs text-fg-muted">
          Recorded automated work
        </p>
      </div>
      <slot name="scope" />
      <div
        class="agent-activity__dates"
        role="group"
        aria-label="Agent usage date range"
      >
        <UiFormField label="Period">
          <UiSelect
            :model-value="period"
            :options="presets"
            @update:model-value="setPeriod"
          />
        </UiFormField>
        <UiFormField label="From">
          <UiInput
            type="date"
            :model-value="filters.start_date"
            @change="setDate('start_date', $event)"
          />
        </UiFormField>
        <UiFormField label="Through">
          <UiInput
            type="date"
            :model-value="filters.end_date"
            @change="setDate('end_date', $event)"
          />
        </UiFormField>
      </div>
    </div>
    <UiCallout
      v-if="error"
      tone="warning"
    >
      {{ error }}<span v-if="summary"> Previously loaded Agent activity is shown for this selection.</span>
    </UiCallout>
    <UiSkeleton
      v-if="loading && !summary"
      shape="block"
      height="32rem"
      aria-label="Loading Agent activity"
    />
    <template v-else-if="summary">
      <div
        class="agent-activity__trends"
        aria-label="Delivery trends"
      >
        <DeliveryTrendChart
          v-for="metric in deliveryMetrics"
          :key="metric.key"
          :metric="metric"
          :totals="summary.delivery_trends.totals"
          :days="summary.delivery_trends.daily"
        />
      </div>
      <div class="agent-activity__analysis">
        <AgentActivityChart
          v-model:field="tokenField"
          :days="summary.daily"
          :totals="summary.totals"
          :timezone="filters.timezone"
        />
        <aside
          class="agent-activity__breakdown"
          aria-label="Agent activity breakdown"
        >
          <div class="agent-activity__breakdown-header">
            <h3 class="text-base font-semibold text-fg-strong">
              Usage by
            </h3>
            <UiButtonGroup aria-label="Group agent usage">
              <UiButton
                v-for="group in groups"
                :key="group.value"
                size="sm"
                :variant="filters.group_by === group.value ? 'secondary' : 'ghost'"
                :aria-pressed="filters.group_by === group.value"
                @click="emit('filters', { group_by: group.value as ActivityGroup })"
              >
                {{ group.label }}
              </UiButton>
            </UiButtonGroup>
          </div>
          <p
            v-if="loading && displayGroup !== filters.group_by"
            class="text-xs text-fg-muted"
            role="status"
          >
            Updating grouping…
          </p>
          <ul
            class="agent-activity__rows"
            :aria-label="`Usage by ${displayGroup}`"
          >
            <li
              v-for="row in rows"
              :key="row.group_key"
              class="agent-activity__row"
            >
              <span
                class="agent-activity__swatch"
                :style="{ backgroundColor: `var(--color-${row.token})` }"
                aria-hidden="true"
              />
              <div class="agent-activity__identity">
                <span
                  class="agent-activity__row-label"
                  :title="row.label"
                >{{ row.label }}</span>
                <span
                  class="agent-activity__track"
                  aria-hidden="true"
                ><span :style="{ width: `${row.width}%`, backgroundColor: `var(--color-${row.token})` }" /></span>
              </div>
              <span
                class="focus-ring agent-activity__row-value"
                tabindex="0"
                :title="exactUsage(row, 'total')"
                :aria-label="`${row.label}. ${exactUsage(row, 'total')}`"
              >{{ row.value == null ? 'N/A' : formatTokens(row.value) }}</span>
            </li>
          </ul>
          <p
            v-if="!rows.length"
            class="text-xs text-fg-muted"
          >
            No recorded usage in this date range.
          </p>
        </aside>
      </div>
      <p class="text-2xs leading-5 text-fg-subtle">
        Based on recorded automated delivery · {{ filters.timezone }} · Updated {{ formatAbsoluteDateTime(summary.as_of) }}
      </p>
      <p
        v-if="summary.undated.launch_attempts"
        class="text-xs text-fg-muted"
      >
        {{ summary.undated.launch_attempts }} {{ summary.undated.launch_attempts === 1 ? 'attempt has' : 'attempts have' }} no recorded start date and {{ summary.undated.launch_attempts === 1 ? 'is' : 'are' }} excluded from this range.
      </p>
    </template>
  </section>
</template>

<style scoped>
.agent-activity { min-width: 0; display: flex; flex-direction: column; gap: 16px; }
.agent-activity__header { display: flex; align-items: end; gap: 16px; }
.agent-activity__heading { margin-right: auto; flex: none; }
.agent-activity__dates { display: flex; gap: 8px; flex: none; }
.agent-activity__dates > * { width: 135px; }
.agent-activity__trends { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
.agent-activity__analysis { display: grid; grid-template-columns: minmax(0, 1.8fr) minmax(340px, 1fr); align-items: stretch; gap: 16px; }
.agent-activity__breakdown { min-width: 0; padding: 16px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); }
.agent-activity__breakdown-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.agent-activity__breakdown-header :deep(button) { padding-left: 8px; padding-right: 8px; font-size: 11px; white-space: nowrap; }
.agent-activity__rows { max-height: 240px; overflow-y: auto; scrollbar-gutter: stable; }
.agent-activity__row { display: flex; align-items: center; gap: 8px; padding: 18px 0; border-bottom: 1px solid var(--color-border-subtle); }
.agent-activity__swatch { width: 9px; height: 9px; flex: none; border-radius: var(--radius-full); }
.agent-activity__identity { min-width: 0; flex: 1; }
.agent-activity__row-label { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.agent-activity__row-value { flex: none; white-space: nowrap; font-size: 12px; font-variant-numeric: tabular-nums; }
.agent-activity__track { display: block; height: 5px; margin-top: 10px; border-radius: var(--radius-full); background: var(--color-border-default); }
.agent-activity__track > span { display: block; height: 100%; border-radius: inherit; }
@media (max-width: 1100px) { .agent-activity__header { flex-wrap: wrap; } }
</style>
