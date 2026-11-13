<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useId } from 'vue'
import { BarController, BarElement, CategoryScale, Chart, LineController, LineElement, LinearScale, PointElement, type ChartConfiguration } from 'chart.js'
import type { SchemaDeliveryTrendDayOut, SchemaDeliveryTrendMetricsOut } from '@/api'
import { useUsageCanvas } from '@/composables/useUsageCanvas'
import { activityDayLabel, deliveryCoverage, deliveryValue, formatDeliveryValue, type deliveryMetrics } from '@/lib/deliveryTrends'

Chart.register(BarController, BarElement, CategoryScale, LinearScale, LineController, LineElement, PointElement)
const props = defineProps<{ metric: (typeof deliveryMetrics)[number]; totals: SchemaDeliveryTrendMetricsOut; days: SchemaDeliveryTrendDayOut[] }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const plot = ref<HTMLElement | null>(null)
const selected = ref<number | null>(null)
const detailId = `trend-${useId()}`
let chart: Chart<'bar' | 'line'> | null = null
let signature = ''
const visible = useUsageCanvas(canvas, paint, plot)
const values = computed<(number | null)[]>(previous => visible.value ? props.days.map(day => deliveryValue(day, props.metric.key)) : previous ?? [])
const total = computed(() => formatDeliveryValue(deliveryValue(props.totals, props.metric.key), props.metric.key))
const focused = computed(() => selected.value == null ? null : props.days[selected.value] ?? null)
const coverage = computed(() => props.totals[props.metric.key].coverage.status)
const review = computed(() => props.totals.first_review_acceptance)

function leavePlot(): void {
  if (document.activeElement !== canvas.value) selected.value = null
}
function move(event: KeyboardEvent): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return
  event.preventDefault()
  selected.value = event.key === 'Escape' || !props.days.length ? null : event.key === 'Home' ? 0 : event.key === 'End' ? props.days.length - 1
    : Math.max(0, Math.min(props.days.length - 1, (selected.value ?? 0) + (event.key === 'ArrowRight' ? 1 : -1)))
}
function hover(event: MouseEvent): void {
  if (document.activeElement === canvas.value || !props.days.length) return
  const bounds = canvas.value!.getBoundingClientRect()
  selected.value = Math.max(0, Math.min(props.days.length - 1, Math.floor((event.clientX - bounds.left) / bounds.width * props.days.length)))
}
function paint(): void {
  if (!canvas.value || !plot.value) return
  const style = getComputedStyle(canvas.value)
  const width = plot.value.clientWidth
  const height = plot.value.clientHeight
  const next = JSON.stringify([values.value, props.days.map(day => day.date), width, height, window.devicePixelRatio, document.documentElement.dataset.theme])
  if (chart && signature === next) return
  signature = next
  const color = style.getPropertyValue(`--color-${props.metric.token}`).trim()
  const line = props.metric.key === 'first_review_acceptance'
  const config: ChartConfiguration<'bar' | 'line'> = {
    type: line ? 'line' : 'bar',
    data: { labels: props.days.map(day => day.date), datasets: [{
      data: values.value, backgroundColor: color, borderColor: color, borderWidth: line ? 1.5 : 0,
      borderRadius: 2, borderSkipped: false, maxBarThickness: 4, pointRadius: 1.5, pointHoverRadius: 1.5, spanGaps: false,
    }] },
    options: {
      responsive: false, animation: false, maintainAspectRatio: false, events: [],
      devicePixelRatio: window.devicePixelRatio || 1,
      scales: { x: { display: false }, y: { display: false, beginAtZero: true, suggestedMax: 1 } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  }
  if (chart) { chart.data = config.data; chart.options = config.options!; chart.resize(width, height); chart.update('none') }
  else { canvas.value.width = width; canvas.value.height = height; chart = new Chart(canvas.value, config) }
}
onBeforeUnmount(() => { chart?.destroy(); chart = null })
</script>

<template>
  <figure
    class="delivery-trend"
    :aria-label="metric.label"
    :data-metric="metric.key"
    :data-visible="visible"
  >
    <figcaption>
      <h3 class="delivery-trend__label">
        {{ metric.label }}
      </h3>
      <p
        class="delivery-trend__total"
        :style="metric.key === 'first_review_acceptance' ? { color: `var(--color-${metric.token})` } : {}"
        :title="`${formatDeliveryValue(deliveryValue(totals, metric.key), metric.key, true)}. ${deliveryCoverage(totals, metric.key)}`"
      >
        {{ total }}
      </p>
      <p
        class="delivery-trend__note"
        :title="deliveryCoverage(totals, metric.key)"
      >
        <template v-if="metric.key === 'first_review_acceptance' && review.reviewed">
          {{ review.accepted }} of {{ review.reviewed }} tickets<template v-if="coverage === 'partial'">
            · Partial
          </template>
        </template>
        <template v-else-if="coverage === 'partial'">
          Partial coverage
        </template>
        <template v-else-if="coverage === 'unavailable'">
          History unavailable
        </template>
        <template v-else-if="metric.key === 'first_review_acceptance'">
          No reviews
        </template>
      </p>
    </figcaption>
    <div
      ref="plot"
      class="delivery-trend__plot"
      @mouseleave="leavePlot"
    >
      <canvas
        ref="canvas"
        class="focus-ring"
        role="img"
        tabindex="0"
        :aria-describedby="detailId"
        :aria-label="`${metric.label} by day. Use arrow keys, Home or End for exact values.`"
        @focus="selected = days.length ? 0 : null"
        @blur="selected = null"
        @keydown="move"
        @mousemove="hover"
      />
      <div
        v-if="focused"
        :id="detailId"
        role="tooltip"
        aria-live="polite"
        class="delivery-trend__tooltip"
      >
        <strong>{{ focused.date }}</strong>
        <span>{{ formatDeliveryValue(deliveryValue(focused, metric.key), metric.key, true) }}</span>
        <span v-if="metric.key === 'first_review_acceptance'">{{ focused.first_review_acceptance.accepted }} of {{ focused.first_review_acceptance.reviewed }} tickets</span>
        <span>{{ deliveryCoverage(focused, metric.key) }}</span>
      </div>
      <span
        v-else
        :id="detailId"
        class="sr-only"
      >{{ deliveryCoverage(totals, metric.key) }} Dates use the selected reporting timezone. Work time sums distinct tasks.</span>
    </div>
    <div
      class="delivery-trend__dates"
      aria-hidden="true"
    >
      <span>{{ days[0] ? activityDayLabel(days[0].date) : '—' }}</span><span>{{ days.length ? activityDayLabel(days[days.length - 1]!.date) : '—' }}</span>
    </div>
  </figure>
</template>

<style scoped>
.delivery-trend { min-width: 0; margin: 0; padding: 12px 10px 10px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); }
.delivery-trend__label { font-size: 11px; font-weight: 600; line-height: 18px; white-space: nowrap; color: var(--color-fg-strong); }
.delivery-trend__total { margin-top: 2px; font-size: 25px; line-height: 32px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
.delivery-trend__note { height: 24px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; line-height: 20px; color: var(--color-fg-muted); }
.delivery-trend__plot { position: relative; height: 48px; border-bottom: 1px solid var(--color-border-default); }
.delivery-trend__plot canvas { display: block; width: 100%; height: 100%; }
.delivery-trend__dates { display: flex; justify-content: space-between; gap: 4px; padding-top: 5px; font-size: 10px; line-height: 16px; color: var(--color-fg-muted); }
.delivery-trend__tooltip { position: absolute; bottom: 54px; left: 0; z-index: var(--z-tooltip); width: 220px; max-width: 80vw; display: grid; gap: 3px; padding: 8px; border: 1px solid var(--color-border-strong); border-radius: var(--radius-sm); background: var(--color-bg-surface); box-shadow: var(--shadow-md); color: var(--color-fg-default); font-size: 11px; line-height: 16px; overflow-wrap: anywhere; }
.delivery-trend:nth-last-child(-n+2) .delivery-trend__tooltip { left: auto; right: 0; }
</style>
