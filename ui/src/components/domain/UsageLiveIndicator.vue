<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiBadge } from '@/components/ui'
import { useUsageFeed } from '@/composables/useUsageFeed'
import { useUsageVisibility } from '@/composables/useUsageCanvas'

defineProps<{ active: boolean }>()
const feed = useUsageFeed()
const root = ref<HTMLElement | null>(null)
const visible = useUsageVisibility(root)
const live = computed(() => feed?.value.status === 'live')
const label = computed(() => live.value ? 'Live' : feed?.value.status === 'connecting' ? 'Connecting' : 'Reconnecting')
</script>

<template>
  <span
    ref="root"
    class="usage-live-indicator-slot"
  >
    <UiBadge
      v-if="active && feed"
      class="usage-live-indicator"
      :class="{ 'usage-live-indicator--live': live, 'usage-live-indicator--motion': live && visible }"
      :tone="live ? 'danger' : 'neutral'"
      variant="outline"
      dot
      :title="live ? 'Live updates connected' : 'Updates paused; showing the last received totals'"
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
