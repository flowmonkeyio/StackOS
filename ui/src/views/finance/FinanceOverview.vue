<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaFinanceProjectionOut } from '@/api'
import DataTable from '@/components/DataTable.vue'
import { UiButton, UiEmptyState, UiMetricCard } from '@/components/ui'
import { metricText, object, objects, refs, text } from '@/lib/finance'
import FinanceOverviewCharts from './FinanceOverviewCharts.vue'
import { contributionRows, overviewCurrencies } from './overviewViewModel'
import { reportingReadiness } from './reportingReadiness'

const props = defineProps<{ projection: SchemaFinanceProjectionOut }>()
const emit = defineEmits<{
  inspect: [recordRefs: string[]]
  drill: [dimension: string, row: Record<string, unknown>]
  records: []
}>()
const currencies = computed(() => overviewCurrencies(props.projection))
const readiness = computed(() => reportingReadiness(props.projection))
const groups = computed(() =>
  Object.entries(object(props.projection.breakdowns)).map(([dimension, value]) => ({
    dimension,
    rows: objects(value),
  })),
)
</script>
<template>
  <section
    class="min-w-0 space-y-5"
    aria-label="Finance overview"
  >
    <UiEmptyState
      v-if="!readiness.hasMetrics"
      icon="chart-bar"
      title="Totals are not available yet"
      description="Check your expenses and income for missing dates or amounts. Existing records are still available to inspect."
    >
      <template #actions>
        <UiButton @click="emit('records')">
          View expenses &amp; income
        </UiButton>
      </template>
    </UiEmptyState>
    <section
      v-for="metric in readiness.hasMetrics ? projection.metrics_by_currency : []"
      :key="text(metric.currency)"
      class="space-y-3"
    >
      <h2 class="t-h3">
        {{ text(metric.currency) }} totals
      </h2>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div
          v-for="name in ['spend', 'revenue', 'profit']"
          :key="name"
        >
          <UiMetricCard
            :label="
              name === 'spend' ? 'Spend' : name === 'revenue' ? 'Revenue' : 'Operating profit'
            "
            :value="metricText(metric[name])"
            :hint="
              text(object(metric[name]).state) === 'partial'
                ? 'Partial total'
                : text(object(metric[name]).state) === 'complete'
                  ? undefined
                  : 'Not available'
            "
          />
          <UiButton
            v-if="refs(object(metric[name]).included_record_refs).length"
            size="sm"
            variant="ghost"
            @click="emit('inspect', refs(object(metric[name]).included_record_refs))"
          >
            Inspect included records
          </UiButton>
          <p
            v-if="projection.comparison"
            class="mt-1 text-xs text-fg-muted"
          >
            Prior:
            {{
              metricText(
                objects(projection.comparison.metrics_by_currency).find(
                  (m) => m.currency === metric.currency,
                )?.[name],
              )
            }}
          </p>
        </div>
      </div>
    </section>
    <FinanceOverviewCharts
      v-if="readiness.hasChartMetrics"
      :currencies="currencies"
      @inspect="emit('inspect', $event)"
    />
    <details
      v-if="readiness.hasMetrics"
      class="space-y-3"
    >
      <summary class="focus-ring cursor-pointer text-sm font-medium">
        Detailed breakdowns
      </summary>
      <div class="space-y-5 pt-2">
        <section
          v-for="group in groups"
          :key="group.dimension"
          class="space-y-2"
        >
          <h3 class="t-h3">
            By {{ group.dimension }}
          </h3>
          <DataTable
            :items="contributionRows(group.rows, projection)"
            :columns="[
              { key: 'label', label: 'Group' },
              { key: 'currency', label: 'Currency' },
              { key: 'spend', label: 'Spend contribution' },
              { key: 'revenue', label: 'Revenue contribution' },
            ]"
            interactive
            :aria-label="`${group.dimension} breakdown`"
            max-height="220px"
            @row-click="emit('drill', group.dimension, $event)"
          />
        </section>
        <section class="space-y-2">
          <h3 class="t-h3">
            Recorded contributions across currencies
          </h3>
          <DataTable
            :items="contributionRows(projection.trend, projection)"
            :columns="[
              { key: 'date', label: 'Date' },
              { key: 'currency', label: 'Currency' },
              { key: 'spend', label: 'Spend contribution' },
              { key: 'revenue', label: 'Revenue contribution' },
            ]"
            aria-label="Finance trend"
            max-height="220px"
          />
        </section>
      </div>
    </details>
  </section>
</template>
