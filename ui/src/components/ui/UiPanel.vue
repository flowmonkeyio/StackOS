<!--
  UiPanel — alias for UiCard with `variant="subtle"` and no header chrome.
  Use for ungrouped surfaces inside a card (legal under no-nested-cards rule).
-->
<script setup lang="ts">
defineProps<{
  padded?: boolean;
}>();
</script>

<template>
  <div
    :class="[
      'ui-panel rounded-lg border border-subtle bg-bg-surface-alt',
      padded !== false && 'p-3',
    ]"
  >
    <slot />
  </div>
</template>
