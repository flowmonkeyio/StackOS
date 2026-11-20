<!--
  UiTooltip — hover/focus tooltip. Pure CSS-positioned via floating-ui-style
  manual placement; avoids portal complexity. Wrap a focusable trigger.

  For long-form contextual help, use UiPopover.
-->
<script setup lang="ts">
import { computed, ref } from 'vue';

export interface UiTooltipProps {
  content: string;
  placement?: 'top' | 'bottom' | 'left' | 'right';
  /** Delay before showing on hover, ms. */
  openDelay?: number;
  /** When true, tooltip stays visible (e.g. for keyboard shortcuts list). */
  open?: boolean;
  disabled?: boolean;
}

const props = withDefaults(defineProps<UiTooltipProps>(), {
  placement: 'top',
  openDelay: 350,
  open: undefined,
});

const visible = ref(false);
let timer: number | null = null;

function show() {
  if (props.disabled) return;
  if (timer) clearTimeout(timer);
  timer = window.setTimeout(() => { visible.value = true; }, props.openDelay);
}
function hide() {
  if (timer) { clearTimeout(timer); timer = null; }
  visible.value = false;
}

const placementClass = computed(() => ({
  top:    'left-1/2 -translate-x-1/2 bottom-full mb-1.5',
  bottom: 'left-1/2 -translate-x-1/2 top-full mt-1.5',
  left:   'right-full mr-1.5 top-1/2 -translate-y-1/2',
  right:  'left-full ml-1.5 top-1/2 -translate-y-1/2',
}[props.placement]));

const isOpen = computed(() => props.open ?? visible.value);
</script>

<template>
  <span
    class="ui-tooltip relative inline-flex"
    @mouseenter="show"
    @mouseleave="hide"
    @focusin="show"
    @focusout="hide"
  >
    <slot />
    <span
      v-if="isOpen && content"
      role="tooltip"
      :class="[
        'ui-tooltip__bubble absolute z-tooltip pointer-events-none whitespace-nowrap rounded-sm bg-bg-inverse text-fg-inverse px-2 py-1 text-2xs font-medium shadow-md',
        placementClass,
      ]"
    >
      {{ content }}
    </span>
  </span>
</template>
