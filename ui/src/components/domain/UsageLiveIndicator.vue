<script setup lang="ts">
import { computed } from 'vue'
import { UiBadge } from '@/components/ui'
import { useUsageFeed } from '@/composables/useUsageFeed'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import { executionAttention } from '@/lib/task-tracker/execution'

const props = defineProps<{ active: boolean; execution?: ActiveExecution | null }>()
const feed = useUsageFeed()
const attention = computed(() => executionAttention(props.execution))
const live = computed(() => feed?.value.status === 'live' && !attention.value)
const label = computed(() => attention.value?.label ?? (live.value ? 'Live' : feed?.value.status === 'connecting' ? 'Connecting' : feed?.value.status === 'paused' ? 'Paused' : 'Reconnecting'))
const title = computed(() => [attention.value?.description, feed?.value.status === 'live' ? 'Live updates connected' : 'Updates paused; showing the last received totals'].filter(Boolean).join(' '))
</script>

<template>
  <UiBadge
    v-if="active && (feed || attention)"
    class="usage-live-indicator"
    :class="{ 'usage-live-indicator--live': live }"
    :tone="attention?.tone ?? (live ? 'danger' : 'neutral')"
    variant="outline"
    dot
    :title="title"
  >
    {{ label }}
  </UiBadge>
</template>

<style scoped>
.usage-live-indicator { justify-content: flex-end; border: 0; padding: 0; background: transparent; color: var(--color-fg-muted); font-size: 10px; line-height: 16px; white-space: nowrap; }
.usage-live-indicator--live { color: var(--color-fg-default); }
</style>
