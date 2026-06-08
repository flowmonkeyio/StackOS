<!--
  UiPopover — anchored panel for richer content (forms, mini-menus).
  Differs from UiTooltip: click-to-open, can contain interactive content.
-->
<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue';

export interface UiPopoverProps {
  modelValue?: boolean;
  placement?: 'bottom-start' | 'bottom-end' | 'top-start' | 'top-end' | 'bottom' | 'top';
  /** Close on outside click. Default true. */
  closeOnClickOutside?: boolean;
  /** Close on Escape. Default true. */
  closeOnEsc?: boolean;
  /** Width — fits trigger by default. */
  width?: number | string;
  ariaLabel?: string;
}

const props = withDefaults(defineProps<UiPopoverProps>(), {
  placement: 'bottom-start',
  closeOnClickOutside: true,
  closeOnEsc: true,
  width: undefined,
  ariaLabel: undefined,
});

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>();

const internalOpen = ref(!!props.modelValue);
const open = computed(() => (props.modelValue === undefined ? internalOpen.value : !!props.modelValue));

const root = ref<HTMLElement | null>(null);
const panel = ref<HTMLElement | null>(null);

function setOpen(v: boolean) {
  if (props.modelValue === undefined) internalOpen.value = v;
  emit('update:modelValue', v);
}
function toggle() { setOpen(!open.value); }

function onDocClick(ev: MouseEvent) {
  if (!open.value || !props.closeOnClickOutside) return;
  if (root.value && !root.value.contains(ev.target as Node)) setOpen(false);
}
function onKey(ev: KeyboardEvent) {
  if (open.value && props.closeOnEsc && ev.key === 'Escape') {
    setOpen(false);
    (root.value?.querySelector('[data-popover-trigger]') as HTMLElement | null)?.focus();
  }
}

onMounted(() => {
  document.addEventListener('mousedown', onDocClick);
  document.addEventListener('keydown', onKey);
});
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick);
  document.removeEventListener('keydown', onKey);
});

function onPanelMounted() {
  panel.value?.focus();
}

const placementClass = computed(() => ({
  'bottom-start': 'top-full left-0 mt-1',
  'bottom-end':   'top-full right-0 mt-1',
  'bottom':       'top-full left-1/2 -translate-x-1/2 mt-1',
  'top-start':    'bottom-full left-0 mb-1',
  'top-end':      'bottom-full right-0 mb-1',
  'top':          'bottom-full left-1/2 -translate-x-1/2 mb-1',
}[props.placement]));
</script>

<template>
  <div
    ref="root"
    class="ui-popover relative inline-block"
  >
    <slot
      name="trigger"
      :toggle="toggle"
      :open="open"
      :set-open="setOpen"
    />
    <transition
      enter-active-class="transition-opacity duration-fast ease-enter"
      enter-from-class="opacity-0"
      leave-active-class="transition-opacity duration-fast ease-exit"
      leave-to-class="opacity-0"
    >
      <div
        v-if="open"
        ref="panel"
        tabindex="-1"
        role="dialog"
        :aria-label="ariaLabel"
        @vue:mounted="onPanelMounted"
        :class="[
          'ui-popover__panel absolute z-popover rounded-md border border-default bg-bg-surface shadow-md focus:outline-none',
          placementClass,
        ]"
        :style="{ width: typeof width === 'number' ? width + 'px' : width }"
      >
        <slot :set-open="setOpen" />
      </div>
    </transition>
  </div>
</template>
