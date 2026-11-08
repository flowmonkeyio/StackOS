<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaUsageSampleOut } from '@/api'
import { stepTrace, type LiveMetric } from '@/lib/liveUsage'
const props = defineProps<{ samples: SchemaUsageSampleOut[]; metric: LiveMetric }>()
const path = computed(() => stepTrace(props.samples, props.metric))
</script>

<template>
  <svg
    v-if="path"
    viewBox="0 0 28 12"
    class="usage-trace"
    :data-metric="metric"
    aria-hidden="true"
  >
    <path
      :d="path"
      fill="none"
      stroke="currentColor"
      stroke-width="1.5"
      vector-effect="non-scaling-stroke"
    />
  </svg>
</template>

<style scoped>
.usage-trace { width: 20px; height: 12px; flex: none; color: var(--color-info-fg); }
.usage-trace[data-metric='cache_read'] { color: var(--color-accent-primary); }
.usage-trace[data-metric='output'] { color: var(--color-success-fg); }
</style>
