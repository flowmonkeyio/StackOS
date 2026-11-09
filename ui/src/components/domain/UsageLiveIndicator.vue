<script setup lang="ts">
import { computed } from 'vue'
import { UiBadge } from '@/components/ui'
import { useUsageFeed } from '@/composables/useUsageFeed'

defineProps<{ active: boolean }>()
const feed = useUsageFeed()
const live = computed(() => feed?.value.status === 'live')
const label = computed(() => live.value ? 'Live' : feed?.value.status === 'connecting' ? 'Connecting' : 'Reconnecting')
</script>

<template>
  <UiBadge
    v-if="active && feed"
    class="usage-live-indicator"
    :class="{ 'usage-live-indicator--live': live }"
    :tone="live ? 'danger' : 'neutral'"
    variant="outline"
    dot
    :title="live ? 'Live updates connected' : 'Updates paused; showing the last received totals'"
  >
    {{ label }}
  </UiBadge>
</template>

<style scoped>
.usage-live-indicator { justify-content: flex-end; border: 0; padding: 0; background: transparent; color: var(--color-fg-muted); font-size: 10px; line-height: 16px; white-space: nowrap; }
.usage-live-indicator--live { color: var(--color-fg-default); }
</style>
