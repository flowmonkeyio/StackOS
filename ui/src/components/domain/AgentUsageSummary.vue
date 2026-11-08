<script setup lang="ts">
import { usageFields, usageValue, formatUsage, exactUsage, type AgentStatsMetrics } from '@/lib/agentActivity'
import { computed, onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import { liveUsageTitle, stepTrace } from '@/lib/liveUsage'
import UsageTrace from './UsageTrace.vue'

const props = withDefaults(defineProps<{ metrics?: AgentStatsMetrics | null; compact?: boolean; execution?: ActiveExecution | null }>(), { metrics: null, compact: false, execution: null })
const fields = usageFields
const root = ref<HTMLElement | null>(null)
const fits = ref(false)
const samples = computed(() => props.execution?.live_usage?.samples ?? [])
const hasTraces = computed(() => props.compact && fields.some((field) => stepTrace(samples.value, field.key)))
let observer: ResizeObserver | null = null
function measure(): void {
  if (!root.value) return
  const metrics = [...root.value.querySelectorAll<HTMLElement>('.agent-usage-summary__metric')]
  const width = metrics.reduce((total, metric, index) => total + ((metric.querySelector('dt') as HTMLElement)?.offsetWidth ?? 0) + ((metric.querySelector('dd') as HTMLElement)?.offsetWidth ?? 0) + 2 + (stepTrace(samples.value, fields[index]!.key) ? 22 : 0), 0) + 8
  fits.value = width <= root.value.clientWidth
}
onMounted(() => { if (typeof ResizeObserver !== 'undefined' && root.value) { observer = new ResizeObserver(measure); observer.observe(root.value) }; measure() })
onUpdated(measure)
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <dl
    ref="root"
    class="agent-usage-summary"
    :class="{ 'agent-usage-summary--compact': compact, 'agent-usage-summary--live': hasTraces }"
    :style="compact ? undefined : { gridTemplateColumns: `repeat(${fields.length}, minmax(0, 1fr))` }"
    aria-label="Recorded token usage"
    :title="liveUsageTitle(execution) || undefined"
  >
    <div
      v-for="field in fields"
      :key="field.key"
      class="agent-usage-summary__metric"
      :data-usage-field="field.key"
    >
      <dt>{{ field.label }}</dt>
      <dd
        :title="`${exactUsage(metrics, field.key)} ${liveUsageTitle(execution)}`.trim()"
        :aria-label="`${exactUsage(metrics, field.key)} ${liveUsageTitle(execution)}`.trim()"
        :class="{ 'agent-usage-summary__unavailable': usageValue(metrics, field.key) == null }"
        tabindex="0"
      >
        {{ formatUsage(usageValue(metrics, field.key)) }}
      </dd>
      <UsageTrace
        v-if="hasTraces && fits"
        :samples="samples"
        :metric="field.key"
      />
    </div>
  </dl>
</template>

<style scoped>
.agent-usage-summary { display: grid; grid-template-columns: minmax(0, 0.8fr) repeat(2, minmax(0, 1fr)); margin: 0; gap: 20px; }
.agent-usage-summary__metric { min-width: 0; padding-right: 16px; }
.agent-usage-summary__metric + .agent-usage-summary__metric { border-left: 1px solid var(--color-border-default); padding-left: 24px; }
dt { color: var(--color-fg-muted); font-size: 13px; line-height: 18px; margin-bottom: 6px; }
dd { width: fit-content; margin: 0; color: var(--color-fg-strong); font-size: 32px; line-height: 38px; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.025em; white-space: nowrap; border-radius: var(--radius-xs); }
dd:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: 2px; }
dd.agent-usage-summary__unavailable { color: var(--color-fg-muted); font-size: 13px; line-height: 38px; font-weight: 500; letter-spacing: 0; }
@media (max-width: 900px) { .agent-usage-summary { gap: 12px; } .agent-usage-summary__metric + .agent-usage-summary__metric { padding-left: 12px; } dd { font-size: 24px; line-height: 30px; } }
.agent-usage-summary--compact { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 10px; }
.agent-usage-summary--compact .agent-usage-summary__metric { display: inline-flex; flex: none; align-items: baseline; gap: 4px; padding: 0; white-space: nowrap; }
.agent-usage-summary--compact .agent-usage-summary__metric + .agent-usage-summary__metric { border: 0; padding: 0; }
.agent-usage-summary--compact dt { color: var(--color-fg-muted); font-size: 11px; line-height: 18px; margin: 0; }
.agent-usage-summary--compact dd { color: var(--color-fg-muted); font-size: 11px; line-height: 18px; font-weight: 500; letter-spacing: 0; }
.agent-usage-summary--live { column-gap: 4px; }
.agent-usage-summary--live .agent-usage-summary__metric { gap: 2px; }
</style>
