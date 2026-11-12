<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaExecutionIdentityOut, SchemaWorkDurationOut } from '@/api'
import { useUsageFeed } from '@/composables/useUsageFeed'
import { formatWorkTime } from '@/lib/stackos/time'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'

const props = defineProps<{
  item: { status: string; work_duration?: SchemaWorkDurationOut | null }
  execution?: SchemaExecutionIdentityOut | null
}>()
const feed = useUsageFeed()
const elapsed = computed(() => {
  // The shared heartbeat invalidates presentation; no per-consumer timer.
  void feed?.value.receivedAt
  const execution = isTerminalTrackerStatus(props.item.status) || (feed && feed.value.status !== 'live') ? null : props.execution
  return formatWorkTime(props.item.work_duration, execution)
})
const partial = computed(() => elapsed.value !== 'N/A' && props.item.work_duration?.coverage === 'partial')
const label = computed(() => `Work time: ${elapsed.value}${partial.value ? ' · Partial coverage' : elapsed.value === 'N/A' ? ' · Unavailable' : ''}`)
defineExpose({ label })
</script>

<template>
  <span
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
