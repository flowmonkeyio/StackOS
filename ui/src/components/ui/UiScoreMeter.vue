<!--
  UiScoreMeter — circular or linear score display.
  Use for quality scores, opportunity strength, and budget health.
-->
<script setup lang="ts">
import { computed } from 'vue';

export interface UiScoreMeterProps {
  /** Score 0..max. */
  value: number;
  max?: number;
  /** When `auto`, tone is chosen by score band (red/amber/emerald). */
  tone?: 'auto' | 'accent' | 'success' | 'warning' | 'danger';
  variant?: 'circular' | 'linear';
  size?: 'sm' | 'md' | 'lg';
  /** Override the displayed label. */
  label?: string;
  /** Hide numeric. */
  noLabel?: boolean;
  /** Custom thresholds for `auto` (defaults: <40 danger, <70 warning, else success). */
  thresholds?: { warning: number; success: number };
}

const props = withDefaults(defineProps<UiScoreMeterProps>(), {
  max: 100,
  tone: 'auto',
  variant: 'circular',
  size: 'md',
  label: undefined,
  thresholds: () => ({ warning: 40, success: 70 }),
});

const percent = computed(() => Math.max(0, Math.min(100, (props.value / props.max) * 100)));

const resolvedTone = computed(() => {
  if (props.tone !== 'auto') return props.tone;
  if (percent.value < props.thresholds.warning) return 'danger';
  if (percent.value < props.thresholds.success) return 'warning';
  return 'success';
});

const stroke = computed(() => ({
  accent:  'var(--color-accent-primary)',
  success: 'var(--color-success-default)',
  warning: 'var(--color-warning-default)',
  danger:  'var(--color-danger-default)',
  auto:    '',
}[resolvedTone.value]));

const sizes = computed(() => ({
  sm: { box: 32, sw: 4, font: 'text-2xs' },
  md: { box: 48, sw: 5, font: 'text-xs' },
  lg: { box: 72, sw: 6, font: 'text-sm' },
}[props.size]));

// Circle math
const radius = computed(() => (sizes.value.box - sizes.value.sw) / 2);
const circumference = computed(() => 2 * Math.PI * radius.value);
const offset = computed(() => circumference.value * (1 - percent.value / 100));
</script>

<template>
  <div
    v-if="variant === 'circular'"
    role="meter"
    :aria-valuemin="0"
    :aria-valuemax="max"
    :aria-valuenow="value"
    :aria-label="label ?? `${value} of ${max}`"
    class="ui-score-meter inline-flex items-center justify-center relative"
    :style="{ width: sizes.box + 'px', height: sizes.box + 'px' }"
  >
    <svg
      :width="sizes.box"
      :height="sizes.box"
      :viewBox="`0 0 ${sizes.box} ${sizes.box}`"
    >
      <circle
        :cx="sizes.box / 2"
        :cy="sizes.box / 2"
        :r="radius"
        fill="none"
        stroke="var(--color-bg-sunken)"
        :stroke-width="sizes.sw"
      />
      <circle
        :cx="sizes.box / 2"
        :cy="sizes.box / 2"
        :r="radius"
        fill="none"
        :stroke="stroke"
        :stroke-width="sizes.sw"
        stroke-linecap="round"
        :stroke-dasharray="circumference"
        :stroke-dashoffset="offset"
        :transform="`rotate(-90 ${sizes.box / 2} ${sizes.box / 2})`"
        style="transition: stroke-dashoffset var(--duration-base) var(--easing-standard)"
      />
    </svg>
    <span
      v-if="!noLabel"
      :class="['absolute font-semibold tabular-nums text-fg-strong', sizes.font]"
    >
      {{ label ?? value }}
    </span>
  </div>
  <div
    v-else
    class="ui-score-meter ui-score-meter--linear flex items-center gap-2 text-sm"
  >
    <div class="flex-1 h-1.5 rounded-full bg-bg-sunken overflow-hidden">
      <div
        class="h-full rounded-full transition-all duration-base ease-standard"
        :style="{ width: percent + '%', background: stroke }"
      />
    </div>
    <span
      v-if="!noLabel"
      class="font-mono tabular-nums text-fg-muted"
    >{{ label ?? `${value}/${max}` }}</span>
  </div>
</template>
