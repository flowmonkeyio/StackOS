<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiBadge } from '@/components/ui'
import { useUsageFeed } from '@/composables/useUsageFeed'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import { executionAttention } from '@/lib/task-tracker/execution'
import { useUsageVisibility } from '@/composables/useUsageCanvas'

const props = defineProps<{ active: boolean; execution?: ActiveExecution | null }>()
const feed = useUsageFeed()
const root = ref<HTMLElement | null>(null)
const visible = useUsageVisibility(root)
const attention = computed(() => executionAttention(props.execution))
const live = computed(() => feed?.value.status === 'live' && !attention.value)
const label = computed(() => attention.value?.label ?? (live.value ? 'Live' : feed?.value.status === 'connecting' ? 'Connecting' : feed?.value.status === 'paused' ? 'Paused' : 'Reconnecting'))
const title = computed(() => [attention.value?.description, feed?.value.status === 'live' ? 'Live updates connected' : 'Updates paused; showing the last received totals'].filter(Boolean).join(' '))
</script>

<template>
  <span
    ref="root"
    class="usage-live-indicator-slot"
  >
    <UiBadge
      v-if="active && (feed || attention)"
      class="usage-live-indicator"
      :class="{ 'usage-live-indicator--live': live, 'usage-live-indicator--motion': live && visible }"
      :tone="attention?.tone ?? (live ? 'danger' : 'neutral')"
      variant="outline"
      dot
      :title="title"
    >
      {{ label }}
    </UiBadge>
  </span>
</template>

<style scoped>
.usage-live-indicator-slot { display: inline-flex; align-items: center; }
.usage-live-indicator { justify-content: flex-end; border: 0; padding: 0; background: transparent; color: var(--color-fg-muted); font-size: 10px; line-height: 16px; white-space: nowrap; }
.usage-live-indicator--live { color: var(--color-fg-default); }
.usage-live-indicator--live :deep(.ui-badge__dot) { position: relative; transform-origin: center; }
.usage-live-indicator--live :deep(.ui-badge__dot)::after { content: ''; position: absolute; inset: -3px; border-radius: inherit; background: radial-gradient(circle, var(--color-danger-default), transparent 70%); opacity: 0.25; transform-origin: center; }
.usage-live-indicator--motion :deep(.ui-badge__dot)::after { animation: usage-live-pulse var(--duration-live-pulse) ease-in-out infinite; }
@keyframes usage-live-pulse { 0%, 100% { opacity: 0.2; } 50% { opacity: 0.7; } }
@media (prefers-reduced-motion: reduce) { .usage-live-indicator--motion :deep(.ui-badge__dot)::after { animation: none; } }
</style>
