<!--
  UiJsonSection — canonical "labeled raw payload" block.

  A consistent heading (h4 text-xs font-medium text-fg-muted) + a UiJsonBlock
  (compact, wrapped). Use for the ONE primary payload of a detail surface that
  should be visible. Secondary/advanced blobs use UiAdvancedJsonPanel
  (collapsed). Replaces the ad-hoc h3/h4 + UiJsonBlock pairs across renderers.
-->
<script setup lang="ts">
import UiJsonBlock from './UiJsonBlock.vue'

withDefaults(
  defineProps<{
    title: string
    data: unknown
    /** inline-in-row 12rem · primary-detail 16rem (default) · full-schema 18rem. */
    maxHeight?: string
    wrap?: boolean
  }>(),
  { maxHeight: '16rem', wrap: true },
)
</script>

<template>
  <section class="ui-json-section">
    <h4 class="mb-1.5 text-xs font-medium text-fg-muted">
      {{ title }}
    </h4>
    <UiJsonBlock
      :data="data"
      :max-height="maxHeight"
      :wrap="wrap"
      density="compact"
      :aria-label="title"
    />
  </section>
</template>
