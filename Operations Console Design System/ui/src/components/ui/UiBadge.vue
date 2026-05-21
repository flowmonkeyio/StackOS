<!--
  UiBadge — small inline label.
  - Tone resolves to a semantic color slot.
  - When used for status, prefer <StatusBadge :domain :status /> which
    pulls from `status.ts` and selects tone/icon for you.
-->
<script setup lang="ts">
import { computed } from 'vue';

export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent';

export interface UiBadgeProps {
  tone?: BadgeTone;
  /** subtle (default): tinted bg + colored fg.   solid: filled.   outline: bordered. */
  variant?: 'subtle' | 'solid' | 'outline';
  size?: 'sm' | 'md';
  /** Show a colored leading dot. Use for "in-flight" / live states. */
  dot?: boolean;
  /** Pulse the dot. */
  pulse?: boolean;
  /** Render as <button> with click handler. */
  interactive?: boolean;
}

type BadgeVariant = NonNullable<UiBadgeProps['variant']>;
type BadgeToneClasses = {
  [Tone in BadgeTone]: {
    [Variant in BadgeVariant]: string;
  };
};

const BADGE_TONE_CLASSES = {
  neutral: { subtle: 'bg-neutral-subtle text-neutral-fg',  solid: 'bg-neutral text-fg-on-accent',  outline: 'border border-neutral-border text-neutral-fg' },
  info:    { subtle: 'bg-info-subtle text-info-fg',        solid: 'bg-info text-fg-on-accent',     outline: 'border border-info-border text-info-fg' },
  success: { subtle: 'bg-success-subtle text-success-fg',  solid: 'bg-success text-fg-on-accent',  outline: 'border border-success-border text-success-fg' },
  warning: { subtle: 'bg-warning-subtle text-warning-fg',  solid: 'bg-warning text-fg-on-accent',  outline: 'border border-warning-border text-warning-fg' },
  danger:  { subtle: 'bg-danger-subtle text-danger-fg',    solid: 'bg-danger text-fg-on-accent',   outline: 'border border-danger-border text-danger-fg' },
  accent:  { subtle: 'bg-accent-subtle text-accent-fg',    solid: 'bg-accent text-fg-on-accent',   outline: 'border border-accent text-accent-fg' },
} satisfies BadgeToneClasses;

const props = withDefaults(defineProps<UiBadgeProps>(), {
  tone: 'neutral',
  variant: 'subtle',
  size: 'sm',
});

defineEmits<{ (e: 'click', ev: MouseEvent): void }>();

const toneClass = computed(() => {
  return BADGE_TONE_CLASSES[props.tone][props.variant];
});

const sizeClass = computed(() =>
  props.size === 'sm' ? 'h-5 px-1.5 text-2xs gap-1' : 'h-6 px-2 text-xs gap-1.5'
);

const dotColor = computed(() => ({
  neutral: 'bg-neutral',
  info:    'bg-info',
  success: 'bg-success',
  warning: 'bg-warning',
  danger:  'bg-danger',
  accent:  'bg-accent',
}[props.tone]));
</script>

<template>
  <component
    :is="interactive ? 'button' : 'span'"
    :type="interactive ? 'button' : undefined"
    :class="[
      'ui-badge inline-flex items-center rounded-xs font-medium leading-none whitespace-nowrap',
      toneClass,
      sizeClass,
      interactive && 'focus-ring hover:opacity-80 transition-opacity',
    ]"
    @click="(ev: MouseEvent) => interactive && $emit('click', ev)"
  >
    <span
      v-if="dot"
      :class="['inline-block w-1.5 h-1.5 rounded-full shrink-0', dotColor, pulse && 'animate-pulse']"
      aria-hidden="true"
    />
    <slot name="iconLeft" />
    <span class="ui-badge__label"><slot /></span>
    <slot name="iconRight" />
  </component>
</template>
