<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SchemaActiveExecutionOut, SchemaExecutionIdentityOut, SchemaWorkDurationOut } from '@/api'
import { useUsageFeed } from '@/composables/useUsageFeed'
import { formatWorkTime } from '@/lib/stackos/time'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'
import { useUsageVisibility } from '@/composables/useUsageCanvas'

const props = withDefaults(defineProps<{
  item: { status: string; work_duration?: SchemaWorkDurationOut | null }
  execution?: (SchemaExecutionIdentityOut & Partial<Pick<SchemaActiveExecutionOut, 'connection_state'>>) | null
  active?: boolean
}>(), { active: true, execution: null })
const feed = useUsageFeed()
const root = ref<HTMLElement | null>(null)
const visible = useUsageVisibility(root)
const liveExecution = computed(() => isTerminalTrackerStatus(props.item.status) || !props.execution
  || props.execution.connection_state === 'disconnected' || props.execution.connection_state === 'needs_attention'
  ? null : !feed || feed.value.status === 'live' ? props.execution : null)
const elapsed = computed<string>(previous => {
  // Retain the footer slot, but do not read duration or the clock outside demand.
  // Reveal reads the current projection through the canonical formatter.
  if (!props.active || !visible.value) return previous ?? 'N/A'
  const execution = liveExecution.value
  if (execution) void feed?.value.receivedAt
  return formatWorkTime(props.item.work_duration, execution)
})
const label = computed(() => `Work time: ${elapsed.value}${elapsed.value === 'N/A' ? ' · Unavailable' : ''}`)
defineExpose({ label })
</script>

<template>
  <span
    ref="root"
    class="active-execution-line__elapsed"
    :aria-label="label"
    :title="label"
  ><span class="active-execution-line__time-value">{{ elapsed }}</span></span>
</template>

<style scoped>
.active-execution-line__elapsed { display: flex; justify-content: flex-end; align-items: baseline; gap: 4px; width: 80px; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.active-execution-line__time-value { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
</style>
