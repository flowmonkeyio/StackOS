<!--
  UiSectionHeader — group heading inside a page or card.
  Smaller than UiPageHeader; right-aligned slot for inline actions.
-->
<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  title: string;
  description?: string;
  /** Heading level (h2..h4). Default h2. */
  as?: 'h2' | 'h3' | 'h4';
}>();

const headingClass = computed(() => {
  if (props.as === 'h3') return 't-h3'
  if (props.as === 'h4') return 'text-sm font-semibold leading-5'
  return 't-h2'
})
</script>

<template>
  <div class="ui-section-header mb-3 flex items-start justify-between gap-3">
    <div class="min-w-0">
      <component
        :is="as ?? 'h2'"
        :class="[headingClass, 'text-fg-strong']"
      >
        {{ title }}
      </component>
      <p
        v-if="description"
        class="text-xs text-fg-muted mt-0.5"
      >
        {{ description }}
      </p>
    </div>
    <div
      v-if="$slots.actions"
      class="flex items-center gap-2 shrink-0"
    >
      <slot name="actions" />
    </div>
  </div>
</template>
