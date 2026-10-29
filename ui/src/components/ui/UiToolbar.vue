<!--
  UiToolbar — horizontal cluster of controls. Use above tables, in dialog
  bodies for filters, etc. Keyboard accessible (role=toolbar).

  Children should be UiButtons / UiInputs / UiSelects.
-->
<script setup lang="ts">
defineProps<{
  ariaLabel?: string;
  /** Compact: tighter spacing, sm controls implied by children. */
  density?: 'compact' | 'comfortable';
  /** Visual treatment. */
  variant?: 'default' | 'sunken' | 'plain';
  /** Sticky to the top of its scroll parent. */
  sticky?: boolean;
}>();
</script>

<template>
  <div
    role="toolbar"
    :aria-label="ariaLabel"
    :class="[
      'ui-toolbar flex flex-wrap items-center',
      variant === 'plain' ? 'gap-2' : density === 'comfortable' ? 'gap-3 px-4 py-3' : 'gap-2 px-3 py-2.5',
      variant === 'plain' ? '' : variant === 'sunken' ? 'bg-bg-surface-alt border-y border-subtle' : 'rounded-lg border border-default bg-bg-surface shadow-xs',
      sticky && 'sticky top-0 z-sticky',
    ]"
  >
    <slot />
    <div
      v-if="$slots.right"
      class="ml-auto flex items-center gap-2"
    >
      <slot name="right" />
    </div>
  </div>
</template>
