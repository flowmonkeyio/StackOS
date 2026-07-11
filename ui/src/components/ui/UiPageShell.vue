<script setup lang="ts">
withDefaults(
  defineProps<{
    spacing?: 'sm' | 'md' | 'lg'
    paddedBottom?: boolean
  }>(),
  {
    spacing: 'md',
    paddedBottom: false,
  },
)
</script>

<template>
  <div
    :class="[
      'ui-page-shell w-full min-w-0',
      spacing === 'sm' ? 'space-y-3' : spacing === 'lg' ? 'space-y-8' : 'space-y-4',
      paddedBottom ? 'pb-20' : '',
    ]"
  >
    <slot />
  </div>
</template>
