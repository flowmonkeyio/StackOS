<!--
  UiPopover — anchored panel for richer content (forms, mini-menus).
  Differs from UiTooltip: click-to-open, can contain interactive content.
-->
<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount, onUpdated } from 'vue';

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
  /** Optional external trigger, for editors opened by a chip or row. */
  anchor?: HTMLElement | null;
  /** Stable fallback when a responsive/overflow trigger disappears. */
  returnFocus?: HTMLElement | null;
}

const props = withDefaults(defineProps<UiPopoverProps>(), {
  placement: 'bottom-start',
  closeOnClickOutside: true,
  closeOnEsc: true,
  width: undefined,
  ariaLabel: undefined,
  anchor: undefined,
  returnFocus: undefined,
});

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>();

const internalOpen = ref(!!props.modelValue);
const open = computed(() => (props.modelValue === undefined ? internalOpen.value : !!props.modelValue));

const root = ref<HTMLElement | null>(null);
const panel = ref<HTMLElement | null>(null);
const position = ref({ left: '0px', top: '0px' });
let wasOpen = false;
const trigger = () => props.anchor?.isConnected ? props.anchor
  : props.returnFocus?.isConnected ? props.returnFocus
    : props.anchor ?? root.value?.querySelector<HTMLElement>('[data-popover-trigger]');
function focusTrigger() { (props.returnFocus?.isConnected ? props.returnFocus : trigger())?.focus({ preventScroll: true }); }
function place() {
  const reference = trigger();
  if (!open.value || !reference || !panel.value) return;
  const rect = reference.getBoundingClientRect();
  const bounds = panel.value.getBoundingClientRect();
  const margin = 8;
  const gap = 4;
  const width = bounds.width;
  const height = bounds.height;
  const below = rect.bottom + gap;
  const above = rect.top - gap - height;
  const wantsTop = props.placement.startsWith('top');
  let top = wantsTop ? above : below;
  if (!wantsTop && below + height > window.innerHeight - margin && above >= margin) top = above;
  if (wantsTop && above < margin && below + height <= window.innerHeight - margin) top = below;
  let left = props.placement.endsWith('end') ? rect.right - width
    : ['top', 'bottom'].includes(props.placement) ? rect.left + (rect.width - width) / 2 : rect.left;
  left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));
  top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));
  const next = { left: `${left}px`, top: `${top}px` };
  if (next.left !== position.value.left || next.top !== position.value.top) position.value = next;
}

function setOpen(v: boolean) {
  if (props.modelValue === undefined) internalOpen.value = v;
  emit('update:modelValue', v);
}
function toggle() { setOpen(!open.value); }

function onDocClick(ev: MouseEvent) {
  if (!open.value || !props.closeOnClickOutside) return;
  if (root.value && !root.value.contains(ev.target as Node) && !trigger()?.contains(ev.target as Node)) setOpen(false);
}
function onKey(ev: KeyboardEvent) {
  if (open.value && props.closeOnEsc && ev.key === 'Escape') {
    setOpen(false);
    ev.preventDefault();
  }
}

onMounted(() => {
  document.addEventListener('mousedown', onDocClick);
  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', place);
  document.addEventListener('scroll', place, true);
});
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick);
  document.removeEventListener('keydown', onKey);
  window.removeEventListener('resize', place);
  document.removeEventListener('scroll', place, true);
});

function onPanelMounted() {
  wasOpen = true;
  place();
  panel.value?.focus();
}
onUpdated(() => {
  if (wasOpen && !open.value) focusTrigger();
  wasOpen = open.value;
  place();
});
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
      enter-active-class="transition duration-fast ease-enter"
      enter-from-class="opacity-0 scale-[0.98]"
      leave-active-class="transition duration-fast ease-exit"
      leave-to-class="opacity-0 scale-[0.98]"
    >
      <div
        v-if="open"
        ref="panel"
        tabindex="-1"
        role="dialog"
        :aria-label="ariaLabel"
        :class="[
          'ui-popover__panel fixed z-popover rounded-lg border border-default bg-bg-surface shadow-md p-1 focus-ring',
        ]"
        :style="{ ...position, width: typeof width === 'number' ? width + 'px' : width, maxWidth: 'calc(100vw - 16px)' }"
        @vue:mounted="onPanelMounted"
      >
        <slot :set-open="setOpen" />
      </div>
    </transition>
  </div>
</template>
