<!--
  UiCountBadge — the one numeric count chip.

  Replaces the two divergent bespoke `h-5 min-w-5 rounded-full tabular-nums`
  medallions (HomeConsole danger / Inbox neutral) and the `<UiBadge>{{ n }}</UiBadge>`
  section counts. Tone neutral by default; `danger`/`info`/etc. for alert counts.
  Use `<UiBadge>` for label chips, this for pure counts.
-->
<script setup lang="ts">
import { computed } from 'vue'

import type { Tone } from '@/design/status'

const props = withDefaults(
  defineProps<{
    value: number | string
    tone?: Tone
  }>(),
  { tone: 'neutral' },
)

const TONE_CLASS: Record<Tone, string> = {
  neutral: 'bg-bg-surface-alt text-fg-muted',
  info: 'bg-info-subtle text-info-fg',
  success: 'bg-success-subtle text-success-fg',
  warning: 'bg-warning-subtle text-warning-fg',
  danger: 'bg-danger-subtle text-danger-fg',
}

const toneClass = computed(() => TONE_CLASS[props.tone])
</script>

<template>
  <span
    :class="[
      'inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-2xs font-semibold tabular-nums',
      toneClass,
    ]"
  >
    {{ value }}
  </span>
</template>
