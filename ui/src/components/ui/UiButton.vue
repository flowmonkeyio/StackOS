<!--
  UiButton — primary action element.

  Variants:
    - primary    : main CTA, filled accent
    - secondary  : default action, surface + border
    - ghost      : low-emphasis, no border
    - danger     : destructive, filled
    - link       : inline, underlined

  Sizes: sm | md | lg
  States: default, hover, active, focus-visible, disabled, loading
-->
<script setup lang="ts">
import { computed } from 'vue'

import { hasIcon } from './icons'
import UiIcon from './UiIcon.vue'

export interface UiButtonProps {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-ghost' | 'link'
  size?: 'sm' | 'md' | 'lg'
  /** Render as <a> when set; otherwise <button>. */
  href?: string
  type?: 'button' | 'submit' | 'reset'
  disabled?: boolean
  loading?: boolean
  /** Stretch to fill parent width. */
  block?: boolean
  /** Icon registry name for leading/trailing icon. */
  iconLeft?: string
  iconRight?: string
  /** When true and only an icon child is provided, becomes square. Use UiIconButton instead when possible. */
  iconOnly?: boolean
  /** Aria label — required when iconOnly. */
  ariaLabel?: string
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
})

defineEmits<{
  (e: 'click', ev: MouseEvent): void
}>()

const isDisabled = computed(() => props.disabled || props.loading)

const variantClass = computed(
  () =>
    ({
      primary:
        'bg-accent text-fg-on-accent shadow-xs hover:bg-accent-hover active:bg-accent-active disabled:bg-fg-disabled disabled:text-fg-inverse disabled:shadow-none',
      secondary:
        'bg-bg-surface text-fg-default border border-default shadow-xs hover:bg-bg-surface-alt hover:border-strong active:bg-bg-sunken disabled:bg-bg-surface disabled:text-fg-disabled disabled:hover:border-default disabled:shadow-none',
      ghost:
        'bg-transparent text-fg-muted hover:bg-bg-sunken hover:text-fg-default active:bg-bg-sunken disabled:text-fg-disabled disabled:hover:bg-transparent',
      danger:
        'bg-danger text-fg-on-accent shadow-xs hover:bg-danger-fg active:bg-danger-fg disabled:bg-fg-disabled disabled:shadow-none',
      'danger-ghost':
        'bg-transparent text-danger-fg hover:bg-danger-subtle active:bg-danger-subtle disabled:text-fg-disabled disabled:hover:bg-transparent',
      link: 'bg-transparent text-fg-link hover:underline underline-offset-2 px-0 py-0 h-auto disabled:text-fg-disabled disabled:no-underline',
    })[props.variant],
)

const sizeClass = computed(
  () =>
    ({
      sm: props.iconOnly ? 'h-7 w-7 px-0 text-xs' : 'h-7 px-2.5 gap-1.5 text-xs',
      md: props.iconOnly ? 'h-8 w-8 px-0 text-sm' : 'h-8 px-3 gap-1.5 text-sm',
      lg: props.iconOnly ? 'h-10 w-10 px-0 text-base' : 'h-10 px-4 gap-2 text-base',
    })[props.size],
)
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
    <span v-if="loading" class="ui-button__spinner" aria-hidden="true">
      <UiIcon name="loader" class="ui-button__icon animate-spin" />
    </span>
    <slot v-else name="iconLeft">
      <UiIcon v-if="hasIcon(iconLeft)" :name="iconLeft" class="ui-button__icon" />
    </slot>
    <span v-if="!iconOnly" class="ui-button__label">
      <slot />
    </span>
    <slot v-if="!iconOnly" name="iconRight">
      <UiIcon v-if="hasIcon(iconRight)" :name="iconRight" class="ui-button__icon" />
    </slot>
    <span v-if="iconOnly && !loading" class="ui-button__icon">
      <slot />
    </span>
  </component>
</template>

<style scoped>
.ui-button {
  line-height: 1;
}
.ui-button__icon {
  width: 1.07em;
  height: 1.07em;
  flex: none;
  stroke-width: 1.8;
}
.ui-button__spinner {
  display: inline-flex;
}
</style>
