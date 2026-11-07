<script setup lang="ts">
import { computed } from 'vue'
import { usageFields, formatTokens, exactUsage, type AgentStatsMetrics } from '@/lib/agentActivity'

const props = withDefaults(defineProps<{ metrics?: AgentStatsMetrics | null; compact?: boolean }>(), { metrics: null, compact: false })
const fields = computed(() => usageFields.filter(field => props.metrics?.reported_usage[field.key] != null))
</script>

<template>
  <div v-if="fields.length">
    <dl
      class="agent-usage-summary"
      :class="{ 'agent-usage-summary--compact': compact }"
      :style="{ gridTemplateColumns: `repeat(${fields.length}, minmax(0, 1fr))` }"
      aria-label="Recorded token usage"
    >
      <div
        v-for="field in fields"
        :key="field.key"
        class="agent-usage-summary__metric"
        :data-usage-field="field.key"
      >
        <dt>{{ field.label }}</dt>
        <dd
          :title="exactUsage(metrics, field.key)"
          :aria-label="exactUsage(metrics, field.key)"
          tabindex="0"
        >
          {{ formatTokens(metrics?.reported_usage[field.key]) }}
        </dd>
      </div>
    </dl>
  </div>
</template>

<style scoped>
.agent-usage-summary { display: grid; grid-template-columns: minmax(0, 0.8fr) repeat(2, minmax(0, 1fr)); margin: 0; gap: 20px; }
.agent-usage-summary__metric { min-width: 0; padding-right: 16px; }
.agent-usage-summary__metric + .agent-usage-summary__metric { border-left: 1px solid var(--color-border-default); padding-left: 24px; }
dt { color: var(--color-fg-muted); font-size: 13px; line-height: 18px; margin-bottom: 6px; }
dd { width: fit-content; margin: 0; color: var(--color-fg-strong); font-size: 32px; line-height: 38px; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.025em; overflow-wrap: anywhere; border-radius: var(--radius-xs); }
dd:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: 2px; }
.agent-usage-summary--compact { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.agent-usage-summary--compact .agent-usage-summary__metric { padding-right: 0; }
.agent-usage-summary--compact .agent-usage-summary__metric + .agent-usage-summary__metric { padding-left: 12px; }
.agent-usage-summary--compact dt { font-size: 12px; margin-bottom: 2px; }
.agent-usage-summary--compact dd { font-size: 16px; line-height: 22px; letter-spacing: 0; }
@media (max-width: 900px) { .agent-usage-summary { gap: 12px; } .agent-usage-summary__metric + .agent-usage-summary__metric { padding-left: 12px; } dd { font-size: 24px; line-height: 30px; } }
</style>
