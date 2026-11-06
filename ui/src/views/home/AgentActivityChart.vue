<script setup lang="ts">
import { onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import { BarController, BarElement, CategoryScale, Chart, LinearScale } from 'chart.js'
import type { AgentStatsRow } from './agentActivity'

Chart.register(BarController, BarElement, CategoryScale, LinearScale)
const props = defineProps<{ rows: AgentStatsRow[]; global?: boolean }>()
const canvas = ref<HTMLCanvasElement | null>(null)
let chart: Chart<'bar'> | null = null
let observer: MutationObserver | null = null
let signature = ''

function paint(): void {
  if (!canvas.value) return
  const next = JSON.stringify([props.rows, props.global, document.documentElement.dataset.theme])
  if (chart && next === signature) return
  signature = next
  const style = getComputedStyle(canvas.value)
  const color = style.getPropertyValue('--color-accent-primary').trim()
  const data = {
    labels: props.rows.slice(0, 8).map(row => `${props.global ? `${row.project_name} #${row.project_id} · ` : ''}${row.agent_name ?? 'Unknown agent'} · ${row.selected_role ?? 'Unknown role'} · ${row.host_key ?? 'Unknown host'} · ${row.model ?? 'Unknown model'}`),
    datasets: [{ label: 'Launch attempts', data: props.rows.slice(0, 8).map(row => row.launch_attempts), backgroundColor: color, barThickness: 12 }],
  }
  const options = {
    indexAxis: 'y' as const, responsive: true, maintainAspectRatio: false, animation: false as const,
    scales: {
      x: {
        beginAtZero: true,
        ticks: { precision: 0, color: style.getPropertyValue('--color-fg-subtle').trim() },
        grid: { color: style.getPropertyValue('--color-border-subtle').trim() },
      },
      y: { display: false },
    },
    plugins: { legend: { display: false } },
  }
  if (chart) { chart.data = data; chart.options = options; chart.update('none') }
  else chart = new Chart(canvas.value, { type: 'bar', data, options })
}
onMounted(() => {
  paint()
  observer = new MutationObserver(paint)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
})
onUpdated(paint)
onBeforeUnmount(() => { observer?.disconnect(); chart?.destroy() })
</script>

<template>
  <figure aria-label="Launch attempts by recorded group">
    <figcaption class="mb-2 text-xs text-fg-muted">
      Launch attempts by recorded group · {{ rows.length > 8 ? 'First 8 groups; all groups in the table below' : 'Groups follow table order' }}
    </figcaption>
    <div
      class="relative min-w-0"
      :style="{ height: `${Math.max(60, Math.min(rows.length, 8) * 22 + 28)}px` }"
    >
      <canvas
        ref="canvas"
        role="img"
        aria-label="Launch attempts by recorded group. Exact values and identities are in the Agent activity breakdown table."
      />
    </div>
  </figure>
</template>
