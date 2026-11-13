<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useId } from 'vue'
import { useUsageCanvas } from '@/composables/useUsageCanvas'
import { BarController, BarElement, CategoryScale, Chart, LinearScale, type ChartOptions } from 'chart.js'
import { UiButton, UiButtonGroup } from '@/components/ui'
import { exactUsage, fieldCoverage, formatTokens, formatUsage, usageFields, usageValue, type AgentStatsDay, type AgentStatsMetrics, type UsageField } from '@/lib/agentActivity'
import { activityDayLabel } from '@/lib/deliveryTrends'

Chart.register(BarController, BarElement, CategoryScale, LinearScale)
const props = defineProps<{ days: AgentStatsDay[]; totals?: AgentStatsMetrics; timezone: string }>()
const field = defineModel<UsageField>('field', { default: 'total' })
const selected = ref<number | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
const plot = ref<HTMLElement | null>(null)
const detailId = `daily-values-${useId()}`
let chart: Chart<'bar'> | null = null
let signature = ''
const visible = useUsageCanvas(canvas, paint, plot)
const fields = [{ key: 'total', label: 'All', token: 'accent-primary' }, ...usageFields] as const
const values = computed<(number | null)[]>(previous => visible.value ? props.days.map(day => usageValue(day, field.value)) : previous ?? [])
const focused = computed(() => selected.value == null ? null : props.days[selected.value] ?? null)
const selectedField = computed(() => fields.find(item => item.key === field.value) ?? fields[0])
function focusDay(index: number): void { selected.value = props.days.length ? index : null }
function moveDay(event: KeyboardEvent): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return
  event.preventDefault()
  if (event.key === 'Escape') { selected.value = null; return }
  focusDay(event.key === 'Home' ? 0 : event.key === 'End' ? props.days.length - 1 : Math.max(0, Math.min(props.days.length - 1, (selected.value ?? 0) + (event.key === 'ArrowRight' ? 1 : -1))))
}
function hover(event: MouseEvent): void {
  if (document.activeElement === canvas.value || !props.days.length || !chart?.scales.x) return
  const bounds = canvas.value!.getBoundingClientRect()
  const index = chart.scales.x.getValueForPixel(event.clientX - bounds.left)
  selected.value = index == null ? null : Math.max(0, Math.min(props.days.length - 1, Math.round(index)))
}
function paint(): void {
  if (!canvas.value || !plot.value) return
  const style = getComputedStyle(canvas.value)
  const width = plot.value.clientWidth
  const height = plot.value.clientHeight
  const next = JSON.stringify([values.value, props.days.map(day => day.date), field.value, width, height, window.devicePixelRatio, document.documentElement.dataset.theme])
  if (chart && signature === next) return
  signature = next
  const token = (name: string) => style.getPropertyValue(`--color-${name}`).trim()
  const data = {
    labels: props.days.map(day => activityDayLabel(day.date)),
    datasets: [{ label: selectedField.value.label, data: values.value, backgroundColor: token(selectedField.value.token), borderSkipped: false as const,
      borderRadius: 3, maxBarThickness: 8, categoryPercentage: 0.8, barPercentage: 0.8 }],
  }
  const options: ChartOptions<'bar'> = {
    responsive: false, maintainAspectRatio: false, animation: false, events: [], devicePixelRatio: window.devicePixelRatio || 1,
    layout: { padding: { right: 4 } },
    scales: {
      x: { grid: { display: false }, ticks: { color: token('fg-muted'), font: { family: style.fontFamily, size: 10 }, maxTicksLimit: 5, maxRotation: 0 }, border: { color: token('border-default') } },
      y: { beginAtZero: true, suggestedMax: 1, afterFit: axis => { axis.width = 45 }, grid: { color: token('border-subtle') }, border: { display: false }, ticks: { color: token('fg-muted'), maxTicksLimit: 4, font: { size: 10 }, callback: tick => formatTokens(Number(tick)).replace(/\.00([MB])$/, '$1') } },
    },
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
  }
  if (chart) { chart.data = data; chart.options = options; chart.resize(width, height); chart.update('none') }
  else { canvas.value.width = width; canvas.value.height = height; chart = new Chart(canvas.value, { type: 'bar', data, options }) }
}
onBeforeUnmount(() => { chart?.destroy(); chart = null })
</script>

<template>
  <figure
    :data-visible="visible"
    class="activity-chart"
    aria-label="Daily recorded agent usage"
  >
    <figcaption>
      <h3 class="text-base font-semibold text-fg-strong">
        Daily tokens
      </h3>
    </figcaption>
    <UiButtonGroup
      aria-label="Token metric"
      class="activity-chart__metrics"
    >
      <UiButton
        v-for="option in fields"
        :key="option.key"
        size="sm"
        :variant="field === option.key ? 'secondary' : 'ghost'"
        :aria-pressed="field === option.key"
        :title="exactUsage(totals, option.key)"
        @click="field = option.key; selected = null"
      >
        <span
          v-if="option.key !== 'total'"
          class="activity-chart__swatch"
          :style="{ backgroundColor: `var(--color-${option.token})` }"
          aria-hidden="true"
        />
        {{ option.label }} <span class="tabular-nums">{{ formatUsage(usageValue(totals, option.key)) }}</span>
      </UiButton>
    </UiButtonGroup>
    <div
      ref="plot"
      class="activity-chart__plot"
      @mouseleave="selected = null"
    >
      <canvas
        ref="canvas"
        class="focus-ring"
        role="img"
        tabindex="0"
        :aria-describedby="detailId"
        :aria-label="`${selectedField.label} tokens per day. Use Left and Right arrow keys, Home or End to inspect exact daily values.`"
        @focus="focusDay(0)"
        @blur="selected = null"
        @keydown="moveDay"
        @mousemove="hover"
      />
      <div
        v-if="focused"
        :id="detailId"
        class="activity-chart__tooltip"
        role="tooltip"
        aria-label="Daily usage details"
        aria-live="polite"
      >
        <strong>{{ focused.date }}</strong>
        <ul>
          <li
            v-for="item in usageFields"
            :key="item.key"
          >
            <span
              class="activity-chart__swatch"
              :style="{ backgroundColor: `var(--color-${item.token})` }"
              aria-hidden="true"
            />
            <span>{{ item.label }}</span><span :title="fieldCoverage(focused, item.key)">{{ formatUsage(usageValue(focused, item.key), false) }}</span>
          </li>
        </ul>
        <footer>Total: {{ formatUsage(usageValue(focused, 'total'), false) }}</footer>
        <p v-if="focused.reported_usage.input_with_unknown_cache">
          Includes {{ formatTokens(focused.reported_usage.input_with_unknown_cache, false) }} input tokens with unknown cache split.
        </p>
        <p v-if="focused.coverage.usage_unavailable_attempts">
          Usage missing for {{ focused.coverage.usage_unavailable_attempts }} attempts.
        </p>
      </div>
      <span
        v-else
        :id="detailId"
        class="sr-only"
      >Hover a bar or focus the chart and use arrow keys for exact values.</span>
    </div>
    <details class="activity-chart__daily-table">
      <summary class="focus-ring">
        Daily values · {{ timezone }}
      </summary>
      <div class="activity-chart__daily-scroll">
        <table aria-label="Exact daily usage">
          <thead>
            <tr>
              <th scope="col">
                Date
              </th><th
                v-for="item in fields"
                :key="item.key"
                scope="col"
              >
                {{ item.key === 'total' ? 'Total' : item.label }}
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
                v-for="item in fields"
                :key="item.key"
                :title="exactUsage(day, item.key)"
              >
                {{ formatUsage(usageValue(day, item.key), false) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </figure>
</template>

<style scoped>
.activity-chart { min-width: 0; margin: 0; padding: 16px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); }
.activity-chart__metrics { display: flex; margin-top: 8px; width: 100%; }
.activity-chart__metrics :deep(button) { min-width: 0; flex: 1; padding: 4px 6px; gap: 5px; font-size: 11px; white-space: nowrap; }
.activity-chart__plot { position: relative; height: 200px; margin-top: 16px; }
.activity-chart__plot canvas { display: block; width: 100%; height: 100%; }
.activity-chart__tooltip { position: absolute; z-index: var(--z-tooltip); top: 4px; right: 4px; width: 210px; max-width: calc(100% - 8px); display: flex; flex-direction: column; gap: 6px; padding: 10px; border: 1px solid var(--color-border-strong); border-radius: var(--radius-sm); background: var(--color-bg-surface); color: var(--color-fg-default); font-size: 11px; line-height: 16px; box-shadow: var(--shadow-md); }
.activity-chart__tooltip strong, .activity-chart__tooltip footer { font-weight: 600; color: var(--color-fg-strong); }
.activity-chart__tooltip li { display: grid; grid-template-columns: 8px minmax(0, 1fr) max-content; align-items: center; gap: 6px; padding: 2px 0; font-variant-numeric: tabular-nums; }
.activity-chart__tooltip footer { border-top: 1px solid var(--color-border-default); padding-top: 4px; text-align: right; }
.activity-chart__swatch { display: inline-block; width: 7px; height: 7px; border-radius: 2px; flex: none; }
.activity-chart__daily-table { margin-top: 8px; color: var(--color-fg-muted); font-size: 10px; line-height: 18px; }
.activity-chart__daily-table summary { cursor: pointer; }
.activity-chart__daily-scroll { max-height: 240px; overflow: auto; }
table { width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 6px 4px; border-bottom: 1px solid var(--color-border-subtle); white-space: nowrap; }
th:first-child { text-align: left; }
</style>
