<!--
  UiMetricCard — single number with optional delta and sparkline slot.
  Use in dashboards. Don't decorate beyond label + value + delta.
-->
<script setup lang="ts">
import { computed } from 'vue';
import { RouterLink } from 'vue-router';

import { hasIcon } from './icons';
import UiIcon from './UiIcon.vue';

export interface UiMetricCardProps {
  label: string;
  value: string | number;
  /** Plain-language explanation shown beneath the value. */
  hint?: string;
  /** Optional unit shown smaller after value. */
  unit?: string;
  /** Numeric delta — positive/negative determines tone unless `deltaTone` set. */
  delta?: number | string;
  deltaLabel?: string;
  /** Forces tone of delta. */
  deltaTone?: 'positive' | 'negative' | 'neutral';
  /** When true, "negative" delta is good (e.g. cost dropping). */
  invertDelta?: boolean;
  /** Display compact density (h: 64px). */
  density?: 'compact' | 'comfortable';
  /** Loading state — shows skeleton. */
  loading?: boolean;
  /** Renders as a RouterLink — the whole card becomes a drill-down. */
  to?: string;
  /** Quiet registry icon shown top-right. */
  icon?: string;
  /** Tints the value (e.g. danger when a failure count is non-zero). */
  valueTone?: 'default' | 'accent' | 'success' | 'warning' | 'danger' | 'info';
}

const props = withDefaults(defineProps<UiMetricCardProps>(), {
  unit: undefined,
  hint: undefined,
  delta: undefined,
  deltaLabel: undefined,
  deltaTone: undefined,
  density: 'comfortable',
  to: undefined,
  icon: undefined,
  valueTone: 'default',
});

const VALUE_TONE_CLASSES = {
  default: 'text-fg-strong',
  accent: 'text-accent-fg',
  success: 'text-success-fg',
  warning: 'text-warning-fg',
  danger: 'text-danger-fg',
  info: 'text-info-fg',
} as const;

const deltaToneResolved = computed(() => {
  if (props.deltaTone) return props.deltaTone;
  if (typeof props.delta !== 'number') return 'neutral';
  if (props.delta === 0) return 'neutral';
  const positive = props.delta > 0;
  const isGood = props.invertDelta ? !positive : positive;
  return isGood ? 'positive' : 'negative';
});

const deltaToneClass = computed(() => ({
  positive: 'text-success-fg',
  negative: 'text-danger-fg',
  neutral:  'text-fg-muted',
}[deltaToneResolved.value]));

const deltaIconName = computed(() => ({
  positive: 'chevron-up',
  negative: 'chevron-down',
  neutral:  'more',
}[deltaToneResolved.value]));
</script>

<template>
  <component
    :is="to ? RouterLink : 'div'"
    :to="to"
    :class="[
      'ui-metric-card relative block rounded-lg border border-default bg-bg-surface shadow-xs',
      density === 'comfortable' ? 'px-4 py-3.5' : 'p-3',
      to && 'focus-ring transition-all duration-fast hover:border-strong hover:shadow-sm',
    ]"
  >
    <UiIcon
      v-if="hasIcon(icon)"
      :name="icon"
      class="absolute right-3.5 top-3.5 h-4 w-4 text-fg-subtle"
      aria-hidden="true"
    />
    <p class="truncate pr-6 text-xs font-medium text-fg-muted">
      {{ label }}
    </p>
    <div class="mt-1.5 flex items-baseline gap-1.5">
      <p
        v-if="loading"
        class="ui-skeleton block bg-bg-sunken animate-pulse rounded-xs"
        :style="{ width: '5ch', height: density === 'comfortable' ? '32px' : '24px' }"
      />
      <template v-else>
        <span
          :class="[
            'font-semibold tabular-nums tracking-tight',
            VALUE_TONE_CLASSES[valueTone],
            density === 'comfortable' ? 'text-2xl' : 'text-xl',
          ]"
        >{{ value }}</span>
        <span
          v-if="unit"
          class="text-sm text-fg-muted"
        >{{ unit }}</span>
      </template>
    </div>
    <p
      v-if="hint"
      class="mt-2 text-xs leading-4 text-fg-subtle"
    >
      {{ hint }}
    </p>
    <div
      v-if="(delta !== undefined && delta !== null) || $slots.spark"
      class="mt-2 flex items-center justify-between gap-2"
    >
      <span
        v-if="delta !== undefined && delta !== null"
        :class="['inline-flex items-center gap-1 text-xs font-medium', deltaToneClass]"
      >
        <UiIcon
          :name="deltaIconName"
          class="h-3 w-3"
          aria-hidden="true"
        />
        <span>{{ delta }}</span>
        <span
          v-if="deltaLabel"
          class="font-normal text-fg-subtle"
        >· {{ deltaLabel }}</span>
      </span>
      <div
        v-if="$slots.spark"
        class="max-w-[60%] flex-1"
      >
        <slot name="spark" />
      </div>
    </div>
  </component>
</template>
