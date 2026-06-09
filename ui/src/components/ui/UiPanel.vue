<!--
  UiPanel — alias for UiCard with `variant="subtle"` and no header chrome.
  Use for ungrouped surfaces inside a card (legal under no-nested-cards rule).
-->
<script setup lang="ts">
withDefaults(defineProps<{
  padded?: boolean;
}>(), {
  padded: true,
});
</script>

<template>
  <div
    :class="[
      'ui-panel rounded-lg border border-subtle bg-bg-surface-alt',
      padded && 'p-3',
    ]"
  >
    <slot />
  </div>
</template>
