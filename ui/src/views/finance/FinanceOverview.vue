<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaFinanceProjectionOut } from '@/api'
import DataTable from '@/components/DataTable.vue'
import { UiButton, UiMetricCard } from '@/components/ui'
import { metricText, object, objects, refs, text } from '@/lib/finance'
import FinanceOverviewCharts from './FinanceOverviewCharts.vue'
import { contributionRows, overviewCurrencies } from './overviewViewModel'

const props = defineProps<{ projection: SchemaFinanceProjectionOut }>()
const emit = defineEmits<{
  inspect: [recordRefs: string[]]
  drill: [dimension: string, row: Record<string, unknown>]
}>()
const currencies = computed(() => overviewCurrencies(props.projection))
const groups = computed(() =>
  Object.entries(object(props.projection.breakdowns)).map(([dimension, value]) => ({
    dimension,
    rows: objects(value),
  })),
)
</script>
<template>
  <section class="min-w-0 space-y-5" aria-label="Finance overview">
    <p v-if="!projection.metrics_by_currency.length" class="text-sm text-fg-muted">
      Metrics unavailable. Records remain inspectable; confirm reporting policy, treatment and
      source coverage.
    </p>
    <section
      v-for="metric in projection.metrics_by_currency"
      :key="text(metric.currency)"
      class="space-y-3"
    >
      <h2 class="t-h2">{{ text(metric.currency) }} · supported operating result</h2>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div v-for="name in ['spend', 'revenue', 'profit']" :key="name">
          <UiMetricCard
            :label="
              name === 'spend' ? 'Spend' : name === 'revenue' ? 'Revenue' : 'Operating profit'
            "
            :value="metricText(metric[name])"
            :hint="text(object(metric[name]).state) || 'Unavailable'"
          />
          <UiButton
            v-if="refs(object(metric[name]).included_record_refs).length"
            size="sm"
            variant="ghost"
            @click="emit('inspect', refs(object(metric[name]).included_record_refs))"
            >Inspect included records</UiButton
          >
          <p v-if="projection.comparison" class="mt-1 text-xs text-fg-muted">
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
    <FinanceOverviewCharts :currencies="currencies" @inspect="emit('inspect', $event)" />
    <p class="text-sm text-fg-muted">
      Breakdowns and trends show spend and revenue contributions from selected supported records. A
      zero revenue contribution means no selected supported income contribution; for expense-only
      records without income coverage, total revenue and operating profit remain unavailable.
    </p>
    <section v-for="group in groups" :key="group.dimension" class="space-y-2">
      <h3 class="t-h3">By {{ group.dimension }}</h3>
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
      <h3 class="t-h3">Recorded contributions across currencies</h3>
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
  </section>
</template>
