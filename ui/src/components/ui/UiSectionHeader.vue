<!--
  UiSectionHeader — group heading inside a page or card.
  Smaller than UiPageHeader; right-aligned slot for inline actions.
  `as` sets the semantic heading level only — the visual is always t-h3.
-->
<script setup lang="ts">
defineProps<{
  title: string;
  description?: string;
  /** Heading level (h2..h4). Default h2. */
  as?: 'h2' | 'h3' | 'h4';
}>();
</script>

<template>
  <div class="ui-section-header mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div class="min-w-0 sm:flex-1">
      <component
        :is="as ?? 'h2'"
        class="t-h3 text-fg-strong"
      >
        {{ title }}
      </component>
      <p
        v-if="description"
        class="mt-0.5 text-sm text-fg-muted"
      >
        {{ description }}
      </p>
    </div>
    <div
      v-if="$slots.actions"
      class="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0 sm:justify-end"
    >
      <slot name="actions" />
    </div>
  </div>
</template>
