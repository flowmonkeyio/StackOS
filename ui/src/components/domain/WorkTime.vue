<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SchemaExecutionIdentityOut, SchemaWorkDurationOut } from '@/api'
import { useUsageFeed } from '@/composables/useUsageFeed'
import { formatWorkTime } from '@/lib/stackos/time'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'
import { useUsageVisibility } from '@/composables/useUsageCanvas'

const props = withDefaults(defineProps<{
  item: { status: string; work_duration?: SchemaWorkDurationOut | null }
  execution?: SchemaExecutionIdentityOut | null
  active?: boolean
}>(), { active: true, execution: null })
const feed = useUsageFeed()
const root = ref<HTMLElement | null>(null)
const visible = useUsageVisibility(root)
const liveExecution = computed(() => isTerminalTrackerStatus(props.item.status) || !props.execution
  ? null : !feed || feed.value.status === 'live' ? props.execution : null)
const measurement = computed<{ elapsed: string; partial: boolean }>(previous => {
  // Retain the footer slot, but do not read duration or the clock outside demand.
  // Reveal reads the current projection through the canonical formatter.
  if (!props.active || !visible.value) return previous ?? { elapsed: 'N/A', partial: false }
  const execution = liveExecution.value
  if (execution) void feed?.value.receivedAt
  const elapsed = formatWorkTime(props.item.work_duration, execution)
  return { elapsed, partial: elapsed !== 'N/A' && props.item.work_duration?.coverage === 'partial' }
})
const elapsed = computed(() => measurement.value.elapsed)
const partial = computed(() => measurement.value.partial)
const label = computed(() => `Work time: ${elapsed.value}${partial.value ? ' · Partial coverage' : elapsed.value === 'N/A' ? ' · Unavailable' : ''}`)
defineExpose({ label })
</script>

<template>
  <span
    ref="root"
    class="active-execution-line__elapsed"
    :aria-label="label"
    :title="label"
  ><span class="active-execution-line__time-value">{{ elapsed }}</span><span
    v-if="partial"
    class="active-execution-line__coverage"
  >Partial</span></span>
</template>

<style scoped>
.active-execution-line__elapsed { display: flex; justify-content: flex-end; align-items: baseline; gap: 4px; width: 80px; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.active-execution-line__time-value { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.active-execution-line__coverage { flex: none; color: var(--color-fg-muted); font-size: 10px; }
</style>
