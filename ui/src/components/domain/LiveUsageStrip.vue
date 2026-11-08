<script setup lang="ts">
import { computed } from 'vue'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import { formatUsage, usageFields } from '@/lib/agentActivity'
import { sampleValue } from '@/lib/liveUsage'
import UsageTrace from './UsageTrace.vue'
const props = defineProps<{ execution?: ActiveExecution | null }>()
const samples = computed(() => props.execution?.live_usage?.samples ?? [])
const latest = computed(() => {
  const live = props.execution?.live_usage
  const measured = live?.usage.measured
  if (!live || !measured || measured.input == null || measured.output == null || measured.total == null) return null
  return { ...measured, at: live.measured_at, input: measured.input, output: measured.output, total: measured.total }
})
</script>

<template>
  <section
    v-if="execution"
    class="live-usage-strip"
    :aria-label="`Current call: ${execution.agent}, started ${new Date(execution.started_at).toLocaleString()}`"
  >
    <div
      class="live-usage-strip__scope"
      :title="`Current call · ${execution.agent} · ${new Date(execution.started_at).toLocaleString()}`"
    >
      Current call · {{ execution.agent }} · {{ new Date(execution.started_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) }}
      <span v-if="execution.live_usage"> · Updated {{ new Date(execution.live_usage.measured_at).toLocaleTimeString() }}</span>
    </div>
    <dl>
      <div
        v-for="field in usageFields"
        :key="field.key"
      >
        <dt>{{ field.label }}</dt>
        <dd>
          {{ formatUsage(latest ? sampleValue(latest, field.key) : null) }}<UsageTrace
            :samples="samples"
            :metric="field.key"
          />
        </dd>
      </div>
    </dl>
  </section>
</template>

<style scoped>
.live-usage-strip { height: 70px; border-top: 1px solid var(--color-border-subtle); padding-top: 6px; overflow: hidden; }
.live-usage-strip__scope { font-size: 10px; line-height: 16px; color: var(--color-fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 2px 0 0; gap: 12px; }
dt { font-size: 11px; line-height: 16px; color: var(--color-fg-muted); }
dd { display: flex; align-items: center; gap: 10px; margin: 0; white-space: nowrap; font-size: 16px; line-height: 22px; font-variant-numeric: tabular-nums; }
.usage-trace { width: 70px; max-width: 45%; height: 20px; }
</style>
