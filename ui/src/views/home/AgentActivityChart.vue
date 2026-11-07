<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUpdated, ref, useId } from 'vue'
import { BarController, BarElement, CategoryScale, Chart, LinearScale, Tooltip, type ChartOptions, type Plugin } from 'chart.js'
import { UiButton, UiButtonGroup } from '@/components/ui'
import { activitySeries, formatTokens, type ActivityView, type AgentStatsDay } from '@/lib/agentActivity'

Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip)
const props = defineProps<{ days: AgentStatsDay[]; timezone: string; global?: boolean }>()
const view = defineModel<ActivityView>('view', { default: 'tokens' })
const selectedDay = ref<number | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
const detailId = `daily-values-${useId()}`
let chart: Chart<'bar'> | null = null
let observer: MutationObserver | null = null
let signature = ''
const title = computed(() => view.value === 'tokens' ? 'Tokens per day' : 'Tokens by agent per day')
const series = computed(() => activitySeries(props.days, view.value, props.global))
const focused = computed(() => selectedDay.value == null ? null : props.days[selectedDay.value] ?? null)
const dailyFields = computed(() => ['input', 'cache_read', 'output', 'total'].filter(key => props.days.some(day => day.launch_attempts > 0 && day.reported_usage[key as 'input'] != null)) as ('input' | 'cache_read' | 'output' | 'total')[])
const dailyLabels = { input: 'Input', cache_read: 'Cache', output: 'Output', total: 'Reported total' }
function dayLabel(date: string): string {
  return new Date(`${date}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}
function value(index: number): number | null {
  const values = series.value.map(item => item.values[index]).filter((amount): amount is number => amount != null)
  return values.length ? values.reduce((sum, amount) => sum + amount, 0) : null
}
function totalLabel(index: number): string { return value(index) === props.days[index]?.reported_usage.total ? 'Reported total' : 'Shown' }
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
    props.days.forEach((_day, index) => {
      const amount = value(index)
      if (amount != null && props.days.length <= 14)
        ctx.fillText(`${totalLabel(index) === 'Shown' ? 'Shown ' : ''}${formatTokens(amount)}`, scales.x!.getPixelForValue(index), Math.max(12, scales.y!.getPixelForValue(amount) - 8))
      if (view.value === 'tokens' && series.value[0]?.key === 'cache_read') {
        const bar = instance.getDatasetMeta(0).data[index] as BarElement | undefined
        const box = bar?.getProps(['x', 'y', 'base', 'width'], true)
        const cached = series.value[0]?.values[index]
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
  const next = JSON.stringify([props.days, props.global, view.value, document.documentElement.dataset.theme])
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
      y: { stacked: true, beginAtZero: true, grid: { color: token('border-default') }, border: { display: false }, ticks: { color: token('fg-muted'), maxTicksLimit: 4, callback: tick => formatTokens(Number(tick)).replace(/\.00([MB])$/, '$1') } },
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        enabled: document.activeElement !== canvas.value,
        backgroundColor: token('bg-surface'), titleColor: token('fg-strong'), bodyColor: token('fg-default'), borderColor: token('border-strong'), borderWidth: 1,
        callbacks: {
          title: items => props.days[items[0]?.dataIndex ?? 0]?.date ?? '',
          label: item => `${item.dataset.label}: ${formatTokens(item.raw as number | null, false)} tokens`,
          footer: items => {
            const index = items[0]?.dataIndex ?? 0
            return value(index) == null ? [] : [`${totalLabel(index)}: ${formatTokens(value(index), false)}`]
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
    :class="{ 'activity-chart--agents': view === 'agents' }"
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
      <UiButtonGroup aria-label="Chart view">
        <UiButton
          v-for="option in (['tokens', 'agents'] as const)"
          :key="option"
          size="sm"
          :variant="view === option ? 'secondary' : 'ghost'"
          :aria-pressed="view === option"
          @click="view = option; selectedDay = null"
        >
          {{ option === 'tokens' ? 'Tokens' : 'Agents' }}
        </UiButton>
      </UiButtonGroup>
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
        <strong>{{ focused.date }}<template v-if="value(selectedDay!) != null"> · {{ totalLabel(selectedDay!) }}: {{ formatTokens(value(selectedDay!), false) }} tokens</template></strong>
        <span
          v-for="item in series.filter(item => item.values[selectedDay!] != null)"
          :key="item.key"
        >{{ item.label }}: {{ formatTokens(item.values[selectedDay!], false) }}</span>
      </template>
      <span v-else>Hover a bar or focus the chart and use arrow keys for exact values.</span>
    </div>
    <details class="activity-chart__daily-table">
      <summary class="focus-ring cursor-pointer text-xs text-fg-muted">
        Daily values
      </summary>
      <div class="activity-chart__daily-scroll">
        <table
          aria-label="Exact daily usage"
          class="mt-2 w-full text-xs tabular-nums"
        >
          <thead>
            <tr>
              <th scope="col">
                Date
              </th><th
                v-for="field in dailyFields"
                :key="field"
                scope="col"
              >
                {{ dailyLabels[field] }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="day in days"
              :key="day.date"
            >
              <th scope="row">
                {{ day.date }}
              </th><td
                v-for="field in dailyFields"
                :key="field"
              >
                <template v-if="day.reported_usage[field] != null">
                  {{ formatTokens(day.reported_usage[field], false) }}
                </template>
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
.activity-chart__plot { position: relative; height: 215px; margin-top: 12px; }
.activity-chart__body { display: grid; grid-template-columns: minmax(0, 1fr) minmax(100px, 140px); align-items: center; gap: 12px; }
.activity-chart__legend { display: flex; flex-direction: column; gap: 12px; margin-top: 12px; max-height: 215px; overflow-y: auto; color: var(--color-fg-default); font-size: 13px; }
.activity-chart__legend li { display: flex; align-items: center; gap: 6px; overflow-wrap: anywhere; }
.activity-chart--agents .activity-chart__body { display: block; }
.activity-chart--agents .activity-chart__legend { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; max-height: 144px; }
.activity-chart__swatch { width: 10px; height: 10px; border-radius: 2px; flex: none; }
.activity-chart__plot canvas:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: 2px; border-radius: var(--radius-xs); }
.activity-chart__exact { min-height: 36px; display: flex; flex-wrap: wrap; gap: 2px 12px; font-size: 12px; line-height: 18px; color: var(--color-fg-muted); padding-top: 8px; }
.activity-chart__exact strong { color: var(--color-fg-default); font-weight: 500; }
.activity-chart__daily-table { margin-top: 4px; }
.activity-chart__daily-scroll { max-height: 240px; overflow-y: auto; }
table { table-layout: fixed; }
thead { position: sticky; top: 0; background: var(--color-bg-surface); }
th, td { text-align: right; padding: 6px 4px; border-bottom: 1px solid var(--color-border-subtle); overflow-wrap: anywhere; }
th:first-child { text-align: left; }
@media (max-width: 1350px) { .activity-chart__body { display: block; } .activity-chart__legend { flex-direction: row; flex-wrap: wrap; gap: 8px 16px; font-size: 12px; } }
</style>
