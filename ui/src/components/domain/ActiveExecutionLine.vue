<script setup lang="ts">
import { computed } from 'vue'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import { activeExecutionTitle } from '@/lib/task-tracker/execution'

const props = defineProps<{ execution?: ActiveExecution | null; detailed?: boolean; focusable?: boolean }>()
const agent = computed(() => props.execution?.agent.replace(/[_-]+/g, ' ') ?? '')
const start = computed(() => props.execution ? new Date(props.execution.started_at) : null)
const time = computed(() => start.value?.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) ?? '')
const full = computed(() => props.execution
  ? activeExecutionTitle(props.execution)
  : '')
</script>

<template>
  <span
    v-if="execution"
    class="active-execution-line focus-ring"
    :class="{ 'active-execution-line--detailed': detailed }"
    :title="full"
    :aria-label="`Active now: ${full}`"
    :tabindex="focusable ? 0 : undefined"
    data-active-execution
  >
    <span class="active-execution-line__agent">{{ agent }}</span>
    <span
      v-if="execution.model"
      class="active-execution-line__model"
    >· {{ execution.model }}</span>
    <time :datetime="execution.started_at">· {{ detailed ? start?.toLocaleString() : time }}</time>
  </span>
</template>

<style scoped>
.active-execution-line { display: flex; min-width: 0; max-width: 100%; align-items: center; gap: 4px; color: var(--color-fg-muted); font-size: 11px; line-height: 18px; white-space: nowrap; }
.active-execution-line::before { content: ''; flex: none; width: 5px; height: 5px; border-radius: var(--radius-full); background: var(--color-info-default); margin-right: 2px; }
.active-execution-line__agent { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.active-execution-line__model { flex: none; max-width: 45%; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.active-execution-line time { flex: none; font-variant-numeric: tabular-nums; }
.active-execution-line--detailed { flex-wrap: wrap; white-space: normal; font-size: 12px; }
.active-execution-line--detailed .active-execution-line__agent,
.active-execution-line--detailed .active-execution-line__model { flex: 0 1 auto; max-width: 100%; overflow: visible; }
</style>
