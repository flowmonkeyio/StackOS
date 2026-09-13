<script setup lang="ts">
import { onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import { BarController, BarElement, CategoryScale, Chart, LinearScale } from 'chart.js'
import { UiButton } from '@/components/ui'
import { trackerStatus } from '@/design/status'
import type { TrackerStatus } from '@/lib/task-tracker/types'
import { ticketStatuses, type TicketCounts } from './ticketOverview'

Chart.register(BarController, BarElement, CategoryScale, LinearScale)

const props = defineProps<{
  counts: TicketCounts
  total: number
  selectedStatus?: TrackerStatus | null
}>()
const emit = defineEmits<{ select: [status: TrackerStatus] }>()
const canvas = ref<HTMLCanvasElement | null>(null)
let chart: Chart<'bar'> | null = null
let observer: MutationObserver | null = null
let signature = ''

function statusColorToken(status: TrackerStatus): string {
  const tone = trackerStatus[status].tone
  return `--color-${tone === 'neutral' ? 'fg-subtle' : `${tone}-default`}`
}

function paint(): void {
  if (!canvas.value) return
  const nextSignature = JSON.stringify([props.counts, props.total, props.selectedStatus, document.documentElement.dataset.theme])
  if (chart && signature === nextSignature) return
  signature = nextSignature
  const style = getComputedStyle(canvas.value)
  const token = (key: string) => style.getPropertyValue(`--color-${key}`).trim()
  const data = {
    labels: ['Tickets'],
    datasets: ticketStatuses.map((status) => {
      const color = style.getPropertyValue(statusColorToken(status)).trim()
      return {
        label: trackerStatus[status].label,
        data: [props.counts[status]],
        stack: 'tickets',
        backgroundColor: color,
        borderColor: status === props.selectedStatus ? token('accent-primary') : color,
        borderWidth: status === props.selectedStatus ? 2 : 0,
        borderSkipped: false as const,
        barThickness: 24,
      }
    }),
  }
  const options = {
    indexAxis: 'y' as const,
    responsive: true,
    maintainAspectRatio: false,
    animation: false as const,
    scales: {
      x: { display: false, stacked: true, min: 0, max: props.total },
      y: { display: false, stacked: true },
    },
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
    onClick: (_event: unknown, elements: Array<{ datasetIndex: number }>) => {
      const index = elements[0]?.datasetIndex
      if (index !== undefined && ticketStatuses[index]) emit('select', ticketStatuses[index]!)
    },
  }
  if (chart) {
    chart.data = data
    chart.options = options
    chart.update('none')
  } else chart = new Chart(canvas.value, { type: 'bar', data, options })
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
  <div class="space-y-4">
    <div class="relative h-6 min-w-0 w-full rounded-sm bg-bg-sunken">
      <canvas
        ref="canvas"
        role="img"
        :aria-label="`Current ticket counts by status: ${total} ${total === 1 ? 'ticket' : 'tickets'}. Use the status buttons to inspect tickets.`"
      />
    </div>
    <p
      v-if="total === 0"
      class="text-xs text-fg-subtle"
    >
      No tickets recorded in this scope.
    </p>
    <div
      class="grid grid-cols-7 gap-1"
      role="group"
      aria-label="Ticket statuses"
    >
      <UiButton
        v-for="status in ticketStatuses"
        :key="status"
        :variant="selectedStatus === status ? 'secondary' : 'ghost'"
        size="sm"
        class="ticket-status-count"
        :aria-pressed="selectedStatus === status"
        :aria-label="`${trackerStatus[status].label}: ${counts[status]} tickets`"
        @click="emit('select', status)"
      >
        <span class="flex min-w-0 flex-col gap-1 text-left">
          <span class="flex min-h-8 items-start gap-1.5 text-xs leading-4">
            <span
              class="mt-1 h-2 w-2 shrink-0 rounded-full"
              :style="{ backgroundColor: `var(${statusColorToken(status)})` }"
              aria-hidden="true"
            />
            <span>{{ trackerStatus[status].label }}</span>
          </span>
          <strong class="text-2xl font-semibold leading-6 text-fg-strong tabular-nums">{{ counts[status] }}</strong>
        </span>
      </UiButton>
    </div>
  </div>
</template>

<style scoped>
.ticket-status-count {
  min-width: 0;
  height: auto;
  min-height: 76px;
  padding: 8px;
  white-space: normal;
}

.ticket-status-count :deep(.ui-button__label) {
  width: 100%;
  min-width: 0;
}
</style>
