<script setup lang="ts">
import UiIcon from './UiIcon.vue'
import UiJsonBlock from './UiJsonBlock.vue'

withDefaults(defineProps<{
  title?: string
  summary?: string
  data: unknown
  open?: boolean
  maxHeight?: string
}>(), {
  title: 'Advanced',
  summary: 'Raw JSON',
  open: false,
  maxHeight: '24rem',
})
</script>

<template>
  <details
    class="group rounded-lg border border-subtle bg-bg-sunken"
    :open="open"
  >
    <summary class="focus-ring flex cursor-pointer list-none items-center gap-1.5 rounded-lg px-3 py-1.5 text-2xs font-medium text-fg-muted transition-colors duration-fast hover:text-fg-default [&::-webkit-details-marker]:hidden">
      <UiIcon
        name="chevron-right"
        class="h-3 w-3 text-fg-subtle transition-transform duration-fast group-open:rotate-90"
        aria-hidden="true"
      />
      {{ title }}
      <span class="font-normal text-fg-muted">{{ summary }}</span>
    </summary>
    <div class="border-t border-subtle p-3">
      <UiJsonBlock
        :data="data"
        :max-height="maxHeight"
        density="compact"
        wrap
        aria-label="Advanced JSON"
      />
    </div>
  </details>
</template>
