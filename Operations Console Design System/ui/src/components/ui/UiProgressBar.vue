<!--
  UiProgressBar — determinate or indeterminate horizontal bar.
-->
<script setup lang="ts">
import { computed } from 'vue';

export interface UiProgressBarProps {
  /** Current value, 0..max. Omit (or pass null) for indeterminate. */
  value?: number | null;
  max?: number;
  /** Tone of the fill. */
  tone?: 'accent' | 'info' | 'success' | 'warning' | 'danger';
  size?: 'xs' | 'sm' | 'md';
  /** Show a numeric label alongside. */
  showLabel?: boolean;
  /** Custom label format. */
  format?: (v: number, max: number) => string;
  ariaLabel?: string;
}

const props = withDefaults(defineProps<UiProgressBarProps>(), {
  value: null,
  max: 100,
  tone: 'accent',
  size: 'sm',
  format: undefined,
  ariaLabel: undefined,
});

const indeterminate = computed(() => props.value == null);
const percent = computed(() => indeterminate.value ? 0 : Math.max(0, Math.min(100, (props.value! / props.max) * 100)));

const heightClass = computed(() => ({ xs: 'h-1', sm: 'h-1.5', md: 'h-2' }[props.size]));
const fillBg = computed(() => ({
  accent:  'bg-accent',
  info:    'bg-info',
  success: 'bg-success',
  warning: 'bg-warning',
  danger:  'bg-danger',
}[props.tone]));
</script>

<template>
  <div class="ui-progressbar flex items-center gap-2">
    <div
      role="progressbar"
      :aria-valuemin="0"
      :aria-valuemax="max"
      :aria-valuenow="indeterminate ? undefined : value!"
      :aria-label="ariaLabel"
      :class="['relative flex-1 overflow-hidden rounded-full bg-bg-sunken', heightClass]"
    >
      <div
        v-if="!indeterminate"
        :class="['absolute inset-y-0 left-0 rounded-full transition-all duration-base ease-standard', fillBg]"
        :style="{ width: percent + '%' }"
      />
      <div
        v-else
        :class="['absolute inset-y-0 w-1/3 rounded-full ui-progressbar__indeterminate', fillBg]"
      />
    </div>
    <span
      v-if="showLabel"
      class="text-2xs font-mono tabular-nums text-fg-muted shrink-0"
    >
      {{ format ? format(value ?? 0, max) : indeterminate ? '…' : `${Math.round(percent)}%` }}
    </span>
  </div>
</template>

<style scoped>
.ui-progressbar__indeterminate {
  animation: ui-progress-slide 1.4s var(--easing-standard) infinite;
}
@keyframes ui-progress-slide {
  0%   { left: -33%; }
  100% { left: 100%; }
}
</style>
