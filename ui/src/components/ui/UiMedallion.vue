<!--
  UiMedallion — the one tone-tinted icon medallion.

  Two shapes on a single size scale: round (h-7 w-7, for feed/attention rows)
  and square (h-8 w-8 rounded-md, for setup/link tiles). Inner icon is always
  h-4 w-4. Replaces the h-9 w-9 + h-[18px] + bg-accent-subtle one-offs in the
  connections panels and PluginsView, and the inline MEDALLION maps in
  ActivityItem / AttentionItemRow.
-->
<script setup lang="ts">
import { computed } from 'vue'

import UiIcon from './UiIcon.vue'
import type { Tone } from '@/design/status'

const props = withDefaults(
  defineProps<{
    icon: string
    tone?: Tone
    shape?: 'round' | 'square'
  }>(),
  { tone: 'neutral', shape: 'round' },
)

const TONE_CLASS: Record<Tone, string> = {
  neutral: 'bg-bg-surface-alt text-fg-muted',
  info: 'bg-info-subtle text-info-fg',
  success: 'bg-success-subtle text-success-fg',
  warning: 'bg-warning-subtle text-warning-fg',
  danger: 'bg-danger-subtle text-danger-fg',
}

const toneClass = computed(() => TONE_CLASS[props.tone])
const shapeClass = computed(() => (props.shape === 'square' ? 'h-8 w-8 rounded-md' : 'h-7 w-7 rounded-full'))
</script>

<template>
  <span
    :class="['inline-flex shrink-0 items-center justify-center', shapeClass, toneClass]"
    aria-hidden="true"
  >
    <UiIcon
      :name="icon"
      class="h-4 w-4"
    />
  </span>
</template>
