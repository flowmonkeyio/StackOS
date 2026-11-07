<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUpdated, ref, useId } from 'vue'
import { BarController, BarElement, CategoryScale, Chart, LinearScale, Tooltip, type ChartOptions, type Plugin } from 'chart.js'
import { UiFormField, UiSelect } from '@/components/ui'
import { activitySeries, fieldCoverage, formatTokens, type ActivityColor, type ActivityMetric, type AgentStatsDay } from '@/lib/agentActivity'

Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip)
const props = defineProps<{ days: AgentStatsDay[]; timezone: string; global?: boolean }>()
const metric = defineModel<ActivityMetric>('metric', { default: 'input' })
const color = defineModel<ActivityColor>('color', { default: 'input_type' })
const selectedDay = ref<number | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
const detailId = `daily-values-${useId()}`
let chart: Chart<'bar'> | null = null
let observer: MutationObserver | null = null
let signature = ''
const metricOptions = [
  { value: 'input', label: 'Input tokens' }, { value: 'cache_read', label: 'Cache tokens' },
  { value: 'output', label: 'Output tokens' }, { value: 'attempts', label: 'Attempts' },
]
const colorOptions = computed(() => [
  { value: 'agent', label: 'Agent' }, { value: 'model', label: 'Selected model' },
  ...(metric.value === 'input' ? [{ value: 'input_type', label: 'Input type' }] : []),
])
const title = computed(() => `${metricOptions.find(item => item.value === metric.value)!.label} per day`)
const series = computed(() => activitySeries(props.days, metric.value, color.value, props.global))
const focused = computed(() => selectedDay.value == null ? null : props.days[selectedDay.value] ?? null)
function dayLabel(date: string): string {
  return new Date(`${date}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}
function value(day: AgentStatsDay): number | null { return metric.value === 'attempts' ? day.launch_attempts : day.reported_usage[metric.value] }
function missing(day: AgentStatsDay): string { return metric.value === 'attempts' ? '' : fieldCoverage(day, metric.value) }
function setMetric(value: string | number | null): void {
  const next = value as ActivityMetric
  metric.value = next
  if (next !== 'input' && color.value === 'input_type') color.value = 'model'
  selectedDay.value = null
}
function focusDay(index: number): void {
  selectedDay.value = index
  if (chart) {
    if (chart.options.plugins?.tooltip) chart.options.plugins.tooltip.enabled = false
    chart.setActiveElements(series.value.map((_, datasetIndex) => ({ datasetIndex, index })))
    chart.update('none')
  }
}
function blurChart(): void {
  selectedDay.value = null
  if (chart) {
    if (chart.options.plugins?.tooltip) chart.options.plugins.tooltip.enabled = true
    chart.setActiveElements([])
    chart.update('none')
  }
}
function moveDay(event: KeyboardEvent): void {
  const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End']
  if (!keys.includes(event.key) || props.days.length === 0) return
  event.preventDefault()
  const current = selectedDay.value ?? 0
  focusDay(event.key === 'Home' ? 0 : event.key === 'End' ? props.days.length - 1 : Math.max(0, Math.min(props.days.length - 1, current + (event.key === 'ArrowRight' ? 1 : -1))))
}
const totalsPlugin: Plugin<'bar'> = {
  id: 'dailyTotals',
  afterDatasetsDraw(instance) {
    const { ctx, scales } = instance
    if (!scales.x || !scales.y) return
    ctx.save()
    ctx.fillStyle = getComputedStyle(instance.canvas).getPropertyValue('--color-fg-strong').trim()
    ctx.font = `600 13px ${getComputedStyle(instance.canvas).fontFamily}`
    ctx.textAlign = 'center'
    props.days.forEach((day, index) => {
      const amount = value(day)
      if (amount != null && props.days.length <= 14)
        ctx.fillText(formatTokens(amount), scales.x!.getPixelForValue(index), Math.max(12, scales.y!.getPixelForValue(amount) - 8))
      if (metric.value === 'input' && color.value === 'input_type') {
        const bar = instance.getDatasetMeta(0).data[index] as BarElement | undefined
        const box = bar?.getProps(['x', 'y', 'base', 'width'], true)
        const cached = day.reported_usage.cache_read
        if (box && box.x != null && box.y != null && cached != null && Math.abs(box.base - box.y) > 32 && box.width > 70) {
          ctx.fillStyle = getComputedStyle(instance.canvas).getPropertyValue('--color-fg-on-accent').trim()
          ctx.fillText(formatTokens(cached), box.x, (box.base + box.y) / 2 + 4)
          ctx.fillStyle = getComputedStyle(instance.canvas).getPropertyValue('--color-fg-strong').trim()
        }
      }
    })
    ctx.restore()
  },
}
function paint(): void {
  if (!canvas.value) return
  const next = JSON.stringify([props.days, props.global, metric.value, color.value, document.documentElement.dataset.theme])
  if (chart && signature === next) return
  signature = next
  const style = getComputedStyle(canvas.value)
  const token = (name: string) => style.getPropertyValue(`--color-${name}`).trim()
  const data = {
    labels: props.days.map(day => dayLabel(day.date)),
    datasets: series.value.map(item => ({
      label: item.label, data: item.values, backgroundColor: token(item.token),
      borderSkipped: false as const, borderRadius: 2, maxBarThickness: 180, categoryPercentage: 0.65, barPercentage: 0.65,
    })),
  }
  const options: ChartOptions<'bar'> = {
    responsive: true, maintainAspectRatio: false, animation: false,
    layout: { padding: { top: 22, right: 8 } },
    interaction: { mode: 'index', intersect: false },
    scales: {
      x: { stacked: true, grid: { display: false }, ticks: { color: token('fg-muted'), font: { family: style.fontFamily, size: 11 }, maxTicksLimit: 8 }, border: { color: token('border-default') } },
      y: { stacked: true, beginAtZero: true, grid: { color: token('border-default') }, border: { display: false }, ticks: { color: token('fg-muted'), maxTicksLimit: 4, callback: tick => formatTokens(Number(tick)).replace(/\.00([MB])$/, '$1'), ...(metric.value === 'attempts' ? { precision: 0 } : {}) } },
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        enabled: document.activeElement !== canvas.value,
        backgroundColor: token('bg-surface'), titleColor: token('fg-strong'), bodyColor: token('fg-default'), borderColor: token('border-strong'), borderWidth: 1,
        callbacks: {
          title: items => props.days[items[0]?.dataIndex ?? 0]?.date ?? '',
          label: item => `${item.dataset.label}: ${formatTokens(item.raw as number | null, false)}${metric.value === 'attempts' ? ' attempts' : ' tokens'}`,
          footer: items => {
            const day = props.days[items[0]?.dataIndex ?? 0]
            return day ? [`Total: ${formatTokens(value(day), false)}`, missing(day)].filter(Boolean) : []
          },
        },
      },
    },
    onHover: (_event, elements) => { if (document.activeElement !== canvas.value) selectedDay.value = elements[0]?.index ?? null },
  }
  if (chart) { chart.data = data; chart.options = options; chart.update('none') }
  else chart = new Chart(canvas.value, { type: 'bar', data, options, plugins: [totalsPlugin] })
}
onMounted(() => {
  paint()
  observer = new MutationObserver(paint)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
})
onUpdated(paint)
onBeforeUnmount(() => { observer?.disconnect(); chart?.destroy(); chart = null })
</script>

<template>
  <figure
    class="activity-chart"
    aria-label="Daily recorded agent usage"
  >
    <figcaption class="activity-chart__header">
      <div>
        <h3 class="text-lg font-semibold text-fg-strong">
          {{ title }}
        </h3>
        <p class="mt-1 text-xs text-fg-muted">
          By attempt start date · {{ timezone }}
        </p>
      </div>
      <div class="activity-chart__controls">
        <UiFormField label="Metric">
          <UiSelect
            :model-value="metric"
            :options="metricOptions"
            @update:model-value="setMetric"
          />
        </UiFormField>
        <UiFormField label="Color by">
          <UiSelect
            v-model="color"
            :options="colorOptions"
          />
        </UiFormField>
      </div>
    </figcaption>
    <div class="activity-chart__body">
      <div class="activity-chart__plot">
        <canvas
          ref="canvas"
          role="img"
          tabindex="0"
          :aria-describedby="detailId"
          :aria-label="`${title}. Use Left and Right arrow keys, Home or End to inspect exact daily values, or open the daily values table.`"
          @focus="focusDay(0)"
          @blur="blurChart"
          @keydown="moveDay"
        />
      </div>
      <ul
        class="activity-chart__legend"
        aria-label="Chart legend"
      >
        <li
          v-for="item in series"
          :key="item.key"
        >
          <span
            class="activity-chart__swatch"
            :style="{ backgroundColor: `var(--color-${item.token})` }"
            aria-hidden="true"
          />{{ item.label }}
        </li>
      </ul>
    </div>
    <div
      :id="detailId"
      class="activity-chart__exact"
      aria-live="polite"
    >
      <template v-if="focused">
        <strong>{{ focused.date }} · {{ formatTokens(value(focused), false) }} {{ metric === 'attempts' ? 'attempts' : 'tokens' }}</strong>
        <span
          v-for="item in series"
          :key="item.key"
        >{{ item.label }}: {{ formatTokens(item.values[selectedDay!], false) }}</span>
        <span v-if="missing(focused)">{{ missing(focused) }}</span>
      </template>
      <span v-else>Hover a bar or focus the chart and use arrow keys for exact values.</span>
    </div>
    <details class="activity-chart__daily-table">
      <summary class="focus-ring cursor-pointer text-xs text-fg-muted">
        Daily values
      </summary>
      <div class="overflow-x-auto">
        <table
          aria-label="Exact daily usage"
          class="mt-2 w-full text-xs tabular-nums"
        >
          <thead>
            <tr>
              <th scope="col">
                Date
              </th><th
                v-for="item in series"
                :key="item.key"
                scope="col"
              >
                {{ item.label }}
              </th><th scope="col">
                Total
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(day, index) in days"
              :key="day.date"
            >
              <th scope="row">
                {{ day.date }}
              </th><td
                v-for="item in series"
                :key="item.key"
              >
                {{ formatTokens(item.values[index], false) }}
              </td><td>
                {{ formatTokens(value(day), false) }}<span
                  v-if="missing(day)"
                  class="block text-fg-muted"
                >{{ missing(day) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </figure>
</template>

<style scoped>
.activity-chart { min-width: 0; margin: 0; }
.activity-chart__header { display: flex; flex-wrap: wrap; align-items: start; justify-content: space-between; gap: 12px; }
.activity-chart__controls { display: grid; grid-template-columns: minmax(120px, 1fr) minmax(140px, 1fr); gap: 12px; }
.activity-chart__plot { position: relative; height: 215px; margin-top: 12px; }
.activity-chart__body { display: grid; grid-template-columns: minmax(0, 1fr) minmax(100px, 140px); align-items: center; gap: 12px; }
.activity-chart__legend { display: flex; flex-direction: column; gap: 12px; margin-top: 12px; color: var(--color-fg-default); font-size: 13px; }
.activity-chart__legend li { display: flex; align-items: center; gap: 6px; overflow-wrap: anywhere; }
.activity-chart__swatch { width: 10px; height: 10px; border-radius: 2px; flex: none; }
.activity-chart__plot canvas:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: 2px; border-radius: var(--radius-xs); }
.activity-chart__exact { min-height: 36px; display: flex; flex-wrap: wrap; gap: 2px 12px; font-size: 12px; line-height: 18px; color: var(--color-fg-muted); padding-top: 8px; }
.activity-chart__exact strong { color: var(--color-fg-default); font-weight: 500; }
.activity-chart__daily-table { margin-top: 4px; }
th, td { text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--color-border-subtle); white-space: nowrap; }
th:first-child { text-align: left; }
@media (max-width: 900px) { .activity-chart__controls { width: 100%; } }
@media (max-width: 1350px) { .activity-chart__body { display: block; } .activity-chart__legend { flex-direction: row; flex-wrap: wrap; gap: 8px 16px; font-size: 12px; } }
</style>
