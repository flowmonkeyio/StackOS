<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js'
import DataTable from '@/components/DataTable.vue'
import type { DataTableColumn } from '@/components/types'
import { UiButton, UiFormField, UiSelect } from '@/components/ui'
import { formatMoney } from '@/lib/finance'
import { drawingScale, type OverviewCurrency, type TotalRow } from './overviewViewModel'

Chart.register(
  BarController,
  BarElement,
  CategoryScale,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
)
const props = defineProps<{ currencies: OverviewCurrency[] }>()
const emit = defineEmits<{ inspect: [recordRefs: string[]] }>()
const chosenCurrency = ref('')
const selected = computed(
  () =>
    props.currencies.find((row) => row.currency === chosenCurrency.value) || props.currencies[0],
)
const totalsCanvas = ref<HTMLCanvasElement | null>(null),
  trendCanvas = ref<HTMLCanvasElement | null>(null)
let totalsChart: Chart<'bar'> | null = null,
  trendChart: Chart<'line'> | null = null
let observer: MutationObserver | null = null,
  signature = '',
  active = true
const totalScale = computed(() =>
  drawingScale(selected.value?.totals.flatMap((row) => [row.current, row.prior]) ?? []),
)
const trendScale = computed(() =>
  drawingScale(selected.value?.trend.flatMap((row) => [row.income, row.expenses]) ?? []),
)
const hasTotals = computed(() => selected.value?.totals.some((row) => row.current || row.prior))
const hasTrend = computed(() => selected.value?.trend.some((row) => row.income || row.expenses))
const totalColumns = computed<DataTableColumn<TotalRow>[]>(() => {
  const columns: DataTableColumn<TotalRow>[] = [
    { key: 'label', label: 'Metric' },
    { key: 'currentText', label: 'Current' },
    { key: 'currentState', label: 'Current coverage' },
  ]
  if (selected.value?.compared)
    columns.push(
      { key: 'priorText', label: 'Prior' },
      { key: 'priorState', label: 'Prior coverage' },
      { key: 'changeText', label: 'Change' },
    )
  return columns
})
function paint() {
  if (!active) return
  if (!selected.value) {
    totalsChart?.destroy()
    totalsChart = null
    trendChart?.destroy()
    trendChart = null
    signature = ''
    return
  }
  const next = JSON.stringify([selected.value, document.documentElement.dataset.theme])
  if (signature === next) return
  signature = next
  const group = selected.value
  const style = getComputedStyle(
    totalsCanvas.value || trendCanvas.value || document.documentElement,
  )
  const token = (key: string) => style.getPropertyValue(`--color-${key}`).trim()
  const axes = {
    x: {
      ticks: { color: token('fg-muted'), font: { family: style.fontFamily } },
      grid: { display: false },
    },
    y: {
      beginAtZero: true,
      ticks: { color: token('fg-muted'), font: { family: style.fontFamily } },
      grid: { color: token('border-subtle') },
    },
  }
  if (totalsCanvas.value) {
    const datasets = [
      {
        label: `Current · ${group.currentWindow}`,
        data: group.totals.map((row) => totalScale.value.value(row.current)),
        backgroundColor: token('fg-strong'),
        borderColor: token('fg-strong'),
        borderWidth: 1,
      },
      ...(group.compared
        ? [
            {
              label: `Prior · ${group.priorWindow}`,
              data: group.totals.map((row) => totalScale.value.value(row.prior)),
              backgroundColor: token('bg-surface'),
              borderColor: token('fg-muted'),
              borderWidth: 2,
            },
          ]
        : []),
    ]
    const data = { labels: group.totals.map((row) => row.label), datasets }
    const options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false as const,
      scales: axes,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (item: { dataIndex: number; datasetIndex: number }) => {
              const row = group.totals[item.dataIndex]
              return `${item.datasetIndex === 0 ? 'Current' : 'Prior'} ${row?.label}: ${item.datasetIndex === 0 ? row?.currentText : row?.priorText}`
            },
          },
        },
      },
    }
    if (totalsChart) {
      totalsChart.data = data
      totalsChart.options = options
      totalsChart.update('none')
    } else totalsChart = new Chart(totalsCanvas.value, { type: 'bar', data, options })
  } else {
    totalsChart?.destroy()
    totalsChart = null
  }
  if (trendCanvas.value) {
    const data = {
      labels: group.trendPoints.map((row) => row?.date || 'Unrecorded interval'),
      datasets: [
        {
          label: 'Income contribution',
          data: group.trendPoints.map((row) => trendScale.value.value(row?.income ?? null)),
          borderColor: token('fg-strong'),
          backgroundColor: token('fg-strong'),
          pointStyle: 'circle' as const,
          borderWidth: 2,
          spanGaps: false,
        },
        {
          label: 'Expense contribution',
          data: group.trendPoints.map((row) => trendScale.value.value(row?.expenses ?? null)),
          borderColor: token('fg-muted'),
          backgroundColor: token('fg-muted'),
          pointStyle: 'rect' as const,
          borderDash: [5, 4],
          borderWidth: 2,
          spanGaps: false,
        },
      ],
    }
    const options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false as const,
      scales: axes,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (item: { dataIndex: number; datasetIndex: number }) => {
              const row = group.trendPoints[item.dataIndex]
              return `${item.datasetIndex === 0 ? 'Income' : 'Expenses'}: ${formatMoney(item.datasetIndex === 0 ? row?.income : row?.expenses)}`
            },
          },
        },
      },
    }
    if (trendChart) {
      trendChart.data = data
      trendChart.options = options
      trendChart.update('none')
    } else trendChart = new Chart(trendCanvas.value, { type: 'line', data, options })
  } else {
    trendChart?.destroy()
    trendChart = null
  }
}
onMounted(() => {
  paint()
  observer = new MutationObserver(() => {
    signature = ''
    paint()
  })
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
})
onUpdated(paint)
onBeforeUnmount(() => {
  active = false
  observer?.disconnect()
  totalsChart?.destroy()
  trendChart?.destroy()
})
</script>

<template>
  <section v-if="selected" class="min-w-0 space-y-5" aria-label="Overview finance graphs">
    <UiFormField
      v-if="currencies.length > 1"
      label="Graph currency"
      help="Currencies are shown separately; amounts are never converted or combined."
    >
      <UiSelect
        :model-value="selected.currency"
        :options="currencies.map((row) => ({ value: row.currency, label: row.currency }))"
        @update:model-value="chosenCurrency = String($event)"
      />
    </UiFormField>
    <section class="min-w-0 space-y-2" aria-label="Income versus expenses">
      <h3 class="t-h3">Income versus expenses · {{ selected.currency }}</h3>
      <p class="text-sm text-fg-muted">
        Current: {{ selected.currentWindow }} · {{ selected.basis }}
      </p>
      <p v-if="selected.compared" class="text-sm text-fg-muted">
        Prior: {{ selected.priorWindow
        }}<template v-if="selected.clamped">
          · Previous-year date was clamped to the supported calendar day.</template
        >
      </p>
      <p class="text-xs text-fg-muted">
        Current coverage: {{ selected.coverage
        }}<template v-if="selected.compared">
          · Prior coverage: {{ selected.priorCoverage }}</template
        >. Unavailable income is not zero; partial totals show supported contributions only.
      </p>
      <p class="text-xs text-fg-muted">
        Current: solid bars<template v-if="selected.compared"> · Prior: outlined bars</template>.
        Negative totals preserve net refunds.
      </p>
      <div v-if="hasTotals" class="relative h-56 min-w-0 w-full">
        <canvas
          ref="totalsCanvas"
          role="img"
          :aria-label="`Income versus expenses in ${selected.currency}, current ${selected.currentWindow}${selected.compared ? ', prior ' + selected.priorWindow : ''}. Exact values and changes in the table below.`"
        />
      </div>
      <p v-else class="text-sm text-fg-muted">
        Totals cannot be plotted with the current amount and precision evidence. Exact source
        availability is shown below.
      </p>
      <p class="text-xs text-fg-muted">{{ totalScale.description }}</p>
      <p v-if="totalScale.belowScaleCount" role="status" class="text-xs text-fg-muted">
        {{ totalScale.belowScaleCount }} nonzero amount(s) are below drawing precision. Inspect
        their exact values in the table.
      </p>
      <DataTable
        :items="selected.totals"
        :columns="totalColumns"
        aria-label="Exact income and expense totals"
      />
      <div class="flex flex-wrap gap-2">
        <template v-for="row in selected.totals" :key="row.id">
          <UiButton
            v-if="row.currentRefs.length"
            size="sm"
            variant="ghost"
            @click="emit('inspect', row.currentRefs)"
            >Inspect current {{ row.label.toLowerCase() }} records</UiButton
          >
          <UiButton
            v-if="selected.compared && row.priorRefs.length"
            size="sm"
            variant="ghost"
            @click="emit('inspect', row.priorRefs)"
            >Inspect prior {{ row.label.toLowerCase() }} records</UiButton
          >
        </template>
      </div>
    </section>
    <section class="min-w-0 space-y-2" aria-label="Current income and expense trend">
      <h3 class="t-h3">Income and expense trend · {{ selected.currency }}</h3>
      <p class="text-sm text-fg-muted">
        {{ selected.currentWindow }} · Current-period supported contributions. Only recorded dates
        are shown; gaps are not zero.
      </p>
      <p class="text-xs text-fg-muted">
        Income: solid line with round points · Expenses: dashed line with square points. Source
        coverage: {{ selected.coverage }}.
      </p>
      <div v-if="hasTrend" class="relative h-56 min-w-0 w-full">
        <canvas
          ref="trendCanvas"
          role="img"
          :aria-label="`Current income and expense contributions in ${selected.currency}, ${selected.currentWindow}. Missing dates are gaps. Exact daily values in the table below.`"
        />
      </div>
      <p v-else class="text-sm text-fg-muted">
        No supported dated contributions can be plotted in this currency and window.
      </p>
      <p class="text-xs text-fg-muted">{{ trendScale.description }}</p>
      <p v-if="trendScale.belowScaleCount" role="status" class="text-xs text-fg-muted">
        {{ trendScale.belowScaleCount }} nonzero contribution(s) are below drawing precision.
        Inspect their exact values below.
      </p>
      <DataTable
        :items="selected.trend"
        :columns="[
          { key: 'date', label: 'Recorded date' },
          { key: 'incomeText', label: 'Income contribution' },
          { key: 'expensesText', label: 'Expense contribution' },
        ]"
        aria-label="Exact current finance trend"
        empty-message="No recorded dates in this currency and window."
        max-height="220px"
        interactive
        @row-click="emit('inspect', $event.includedRefs)"
      />
    </section>
  </section>
</template>
