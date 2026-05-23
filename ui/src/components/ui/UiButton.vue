<!--
  UiButton — primary action element.

  Variants:
    - primary    : main CTA, filled accent
    - secondary  : default action, outlined
    - ghost      : low-emphasis, no border
    - danger     : destructive
    - link       : inline, underlined

  Sizes: sm | md | lg
  States: default, hover, active, focus-visible, disabled, loading
-->
<script setup lang="ts">
import { computed } from 'vue';

export interface UiButtonProps {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'link';
  size?: 'sm' | 'md' | 'lg';
  /** Render as <a> when set; otherwise <button>. */
  href?: string;
  type?: 'button' | 'submit' | 'reset';
  disabled?: boolean;
  loading?: boolean;
  /** Stretch to fill parent width. */
  block?: boolean;
  /** Lucide icon name for leading icon (rendered by parent). */
  iconLeft?: string;
  iconRight?: string;
  /** When true and only an icon child is provided, becomes square. Use UiIconButton instead when possible. */
  iconOnly?: boolean;
  /** Aria label — required when iconOnly. */
  ariaLabel?: string;
}

const props = withDefaults(defineProps<UiButtonProps>(), {
  variant: 'secondary',
  size: 'md',
  href: undefined,
  type: 'button',
  disabled: false,
  loading: false,
  block: false,
  iconLeft: undefined,
  iconRight: undefined,
  iconOnly: false,
  ariaLabel: undefined,
});

const BUTTON_ICON_PATHS: Record<string, string[]> = {
  ban: [
    'M4.93 4.93 19.07 19.07',
    'M20 12a8 8 0 0 1-12.12 6.84A8 8 0 0 1 5.16 6.12 8 8 0 0 1 20 12Z',
  ],
  'external-link': [
    'M15 3h6v6',
    'M10 14 21 3',
    'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6',
  ],
  plus: ['M5 12h14', 'M12 5v14'],
  'plug-zap': [
    'M13 2 11 9h7l-5 13',
    'M9 8V2',
    'M15 8V2',
    'M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z',
  ],
  'rotate-ccw': ['M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8', 'M3 3v5h5'],
  save: [
    'M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z',
    'M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7',
    'M7 3v4a1 1 0 0 0 1 1h7',
  ],
};

defineEmits<{
  (e: 'click', ev: MouseEvent): void;
}>();

const isDisabled = computed(() => props.disabled || props.loading);

const variantClass = computed(() => ({
  primary:
    'bg-accent text-fg-on-accent border border-accent hover:bg-accent-hover active:bg-accent-active disabled:bg-fg-disabled disabled:border-fg-disabled disabled:text-fg-inverse',
  secondary:
    'bg-bg-surface text-fg-default border border-default hover:bg-bg-surface-alt hover:border-strong active:bg-bg-sunken disabled:bg-bg-surface disabled:text-fg-disabled',
  ghost:
    'bg-transparent text-fg-default border border-transparent hover:bg-bg-surface-alt active:bg-bg-sunken disabled:text-fg-disabled',
  danger:
    'bg-danger text-fg-on-accent border border-danger hover:bg-danger/90 active:bg-danger/80 disabled:bg-fg-disabled disabled:border-fg-disabled',
  link:
    'bg-transparent text-fg-link border border-transparent hover:underline underline-offset-2 px-0 py-0 h-auto disabled:text-fg-disabled',
}[props.variant]));

const sizeClass = computed(() => ({
  sm: props.iconOnly ? 'h-7 w-7 px-0 text-sm'  : 'h-7 px-2.5 gap-1.5 text-sm',
  md: props.iconOnly ? 'h-8 w-8 px-0 text-sm'  : 'h-8 px-3 gap-2 text-sm',
  lg: props.iconOnly ? 'h-10 w-10 px-0 text-base' : 'h-10 px-4 gap-2 text-base',
}[props.size]));

function iconPaths(name: string | undefined): string[] {
  return name ? BUTTON_ICON_PATHS[name] ?? [] : [];
}
</script>

<template>
  <component
    :is="href ? 'a' : 'button'"
    :href="href"
    :type="href ? undefined : type"
    :disabled="!href && isDisabled"
    :aria-disabled="isDisabled || undefined"
    :aria-busy="loading || undefined"
    :aria-label="ariaLabel"
    :class="[
      'ui-button focus-ring inline-flex items-center justify-center font-medium rounded-sm transition-colors duration-fast ease-standard select-none whitespace-nowrap',
      variantClass,
      sizeClass,
      block && 'w-full',
      isDisabled && 'cursor-not-allowed',
      loading && 'relative',
    ]"
    @click="(ev: MouseEvent) => !isDisabled && $emit('click', ev)"
  >
    <span
      v-if="loading"
      class="ui-button__spinner"
      aria-hidden="true"
    >
      <svg
        class="animate-spin"
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
      >
        <circle
          cx="12"
          cy="12"
          r="9"
          stroke="currentColor"
          stroke-opacity="0.25"
          stroke-width="3"
        />
        <path
          d="M21 12a9 9 0 0 0-9-9"
          stroke="currentColor"
          stroke-width="3"
          stroke-linecap="round"
        />
      </svg>
    </span>
    <slot
      v-else
      name="iconLeft"
    >
      <svg
        v-if="iconPaths(iconLeft).length"
        class="ui-button__icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path
          v-for="path in iconPaths(iconLeft)"
          :key="path"
          :d="path"
        />
      </svg>
    </slot>
    <span
      v-if="!iconOnly"
      class="ui-button__label"
    >
      <slot />
    </span>
    <slot
      v-if="!iconOnly"
      name="iconRight"
    >
      <svg
        v-if="iconPaths(iconRight).length"
        class="ui-button__icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path
          v-for="path in iconPaths(iconRight)"
          :key="path"
          :d="path"
        />
      </svg>
    </slot>
    <span
      v-if="iconOnly && !loading"
      class="ui-button__icon"
    >
      <slot />
    </span>
  </component>
</template>

<style scoped>
.ui-button { line-height: 1; }
.ui-button__icon { width: 1em; height: 1em; flex: none; }
.ui-button__spinner { display: inline-flex; }
</style>
