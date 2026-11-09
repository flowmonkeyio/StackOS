<script setup lang="ts">
import { usageFields, usageValue, formatUsage, exactUsage, type AgentStatsMetrics } from '@/lib/agentActivity'
import { computed } from 'vue'
import UsageTrace from './UsageTrace.vue'

const props = withDefaults(defineProps<{ metrics?: AgentStatsMetrics | null; compact?: boolean }>(), { metrics: null, compact: false })
const fields = computed(() => usageFields.map(field => ({
  ...field,
  value: usageValue(props.metrics, field.key),
  formatted: formatUsage(usageValue(props.metrics, field.key)),
  exact: exactUsage(props.metrics, field.key),
})))
const samples = computed(() => props.metrics?.samples ?? [])
const scopeTitle = 'Recorded totals and measured charts cover the same task or ticket lifetime, including delivery and review. No intermediate history is inferred.'
</script>

<template>
  <dl
    class="agent-usage-summary"
    :class="{ 'agent-usage-summary--compact': compact }"
    :style="compact ? undefined : { gridTemplateColumns: `repeat(${fields.length}, minmax(0, 1fr))` }"
    aria-label="Recorded token usage"
    :title="compact ? scopeTitle : undefined"
  >
    <div
      v-for="field in fields"
      :key="field.key"
      class="agent-usage-summary__metric"
      :data-usage-field="field.key"
    >
      <dt>{{ field.label }}</dt>
      <dd
        :title="field.exact"
        :aria-label="field.exact"
        :class="{ 'agent-usage-summary__unavailable': field.value == null }"
        tabindex="0"
      >
        {{ field.formatted }}
      </dd>
      <UsageTrace
        v-if="compact"
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
.agent-usage-summary--compact { width: 100%; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.agent-usage-summary--compact .agent-usage-summary__metric { display: grid; grid-template-rows: 16px 20px 18px; align-items: center; padding: 0; white-space: nowrap; }
.agent-usage-summary--compact .agent-usage-summary__metric + .agent-usage-summary__metric { border: 0; padding: 0; }
.agent-usage-summary--compact dt { color: var(--color-fg-muted); font-size: 11px; line-height: 16px; margin: 0; }
.agent-usage-summary--compact dd { width: 100%; overflow: hidden; text-overflow: ellipsis; color: var(--color-fg-default); font-size: 13px; line-height: 20px; font-weight: 500; letter-spacing: 0; }
.agent-usage-summary--compact dd.agent-usage-summary__unavailable { color: var(--color-fg-muted); }
</style>
