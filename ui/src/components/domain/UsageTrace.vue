<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaUsageSampleOut } from '@/api'
import { sampleValue, stepTrace, type LiveMetric } from '@/lib/liveUsage'
const props = defineProps<{ samples: SchemaUsageSampleOut[]; metric: LiveMetric }>()
const path = computed(() => stepTrace(props.samples, props.metric))
const hasPoint = computed(() => {
  const latest = props.samples.at(-1)
  return latest != null && sampleValue(latest, props.metric) != null
})
</script>

<template>
  <svg
    viewBox="0 0 28 12"
    class="usage-trace"
    :data-metric="metric"
    :data-state="path ? 'trace' : hasPoint ? 'point' : 'empty'"
    aria-hidden="true"
  >
    <path
      v-if="path"
      :d="path"
      fill="none"
      stroke="currentColor"
      stroke-width="1.5"
      vector-effect="non-scaling-stroke"
    />
    <circle
      v-else-if="hasPoint"
      cx="14"
      cy="6"
      r="1.7"
      fill="currentColor"
    />
    <line
      v-else
      x1="12"
      x2="16"
      y1="6"
      y2="6"
      stroke="currentColor"
      stroke-width="1"
    />
  </svg>
</template>

<style scoped>
.usage-trace { width: 20px; height: 12px; flex: none; color: var(--color-info-fg); }
.usage-trace[data-metric='cache_read'] { color: var(--color-accent-primary); }
.usage-trace[data-metric='output'] { color: var(--color-success-fg); }
.usage-trace[data-state='empty'] { color: var(--color-fg-muted); opacity: 0.5; }
</style>
