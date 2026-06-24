<!--
  UiEmptyState — for zero-data states. Should always include guidance and
  ideally one primary action.
-->
<script setup lang="ts">
import { computed } from 'vue';
import { hasIcon } from './icons';
import UiIcon from './UiIcon.vue';

const props = defineProps<{
  title: string;
  description?: string;
  /** Icon registry name — rendered in the ring; slot[icon] overrides. */
  icon?: string;
  /** Compact (inline) variant — for empty table rows, inside cards. */
  size?: 'sm' | 'md' | 'lg';
  /** Wrap in a dashed-border surface when not already inside a card. */
  framed?: boolean;
}>();

const fallbackIcon = computed(() => (hasIcon(props.icon) ? props.icon : 'info'));
</script>

<template>
  <div
    :class="[
      'ui-empty-state flex flex-col items-center text-center mx-auto',
      size === 'sm' ? 'py-6 max-w-sm gap-2' : size === 'lg' ? 'py-16 max-w-md gap-4' : 'py-10 max-w-md gap-3',
      framed && 'w-full max-w-none rounded-lg border border-dashed border-default bg-bg-surface px-4',
    ]"
  >
    <div
      v-if="$slots.icon || icon"
      :class="[
        'ui-empty-state__icon flex items-center justify-center rounded-full bg-bg-sunken text-fg-subtle',
        size === 'sm' ? 'h-9 w-9 text-lg' : size === 'lg' ? 'h-12 w-12 text-2xl' : 'h-10 w-10 text-xl',
      ]"
    >
      <slot name="icon">
        <UiIcon
          :name="fallbackIcon"
          :class="size === 'sm' ? 'h-4 w-4' : size === 'lg' ? 'h-6 w-6' : 'h-5 w-5'"
          aria-hidden="true"
        />
      </slot>
    </div>
    <div class="flex flex-col gap-1">
      <p class="t-h3 text-fg-strong">
        {{ title }}
      </p>
      <p
        v-if="description"
        class="text-sm text-fg-muted max-w-sm text-balance"
      >
        {{ description }}
      </p>
    </div>
    <div
      v-if="$slots.actions"
      class="flex flex-wrap justify-center gap-2 mt-1"
    >
      <slot name="actions" />
    </div>
  </div>
</template>
