<!--
  UiSidePanel — slide-in panel anchored to viewport edge.
  Use for: detail views (e.g. clicking a row in a list), filters,
  multi-step setup. NOT for navigation menus on desktop.
-->
<script lang="ts">
let sidePanelIdSeed = 0;
let sidePanelStackSeed = 0;
const openSidePanelStack: number[] = [];
let previousBodyOverflow: string | null = null;
const SIDEPANEL_Z_INDEX_BASE = 1100;
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue';
import UiIconButton from './UiIconButton.vue';

export interface UiSidePanelProps {
  modelValue: boolean;
  title?: string;
  description?: string;
  side?: 'right' | 'left';
  size?: 'sm' | 'md' | 'lg' | 'xl';
  /** When true, content scrolls within the panel; header/footer stay fixed. */
  scrollBody?: boolean;
  hideClose?: boolean;
  staticBackdrop?: boolean;
}

const props = withDefaults(defineProps<UiSidePanelProps>(), {
  title: undefined,
  description: undefined,
  side: 'right',
  size: 'md',
  scrollBody: true,
});

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'close'): void;
}>();

const sidePanelId = ++sidePanelIdSeed;
const panelRef = ref<HTMLElement | null>(null);
const overlayRef = ref<HTMLElement | null>(null);
let previousFocus: HTMLElement | null = null;
let stackIndex: number | null = null;

function close() {
  emit('update:modelValue', false);
  emit('close');
}

function removeFromStack() {
  if (stackIndex === null) return;
  const idx = openSidePanelStack.indexOf(stackIndex);
  if (idx >= 0) openSidePanelStack.splice(idx, 1);
  stackIndex = null;
  if (openSidePanelStack.length === 0) {
    document.body.style.overflow = previousBodyOverflow ?? '';
    previousBodyOverflow = null;
  }
}

function onKey(ev: KeyboardEvent) {
  if (!props.modelValue || !isTopSidePanel()) return;
  if (ev.key === 'Escape') {
    ev.preventDefault();
    close();
    ev.stopImmediatePropagation();
    ev.stopPropagation();
  } else if (ev.key === 'Tab') {
    trapFocus(ev);
  }
}

function trapFocus(ev: KeyboardEvent) {
  const node = panelRef.value;
  if (!node) return;
  const focusables = node.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
  );
  if (!focusables.length) {
    ev.preventDefault();
    node.focus();
    return;
  }
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (ev.shiftKey && document.activeElement === first) {
    last.focus();
    ev.preventDefault();
  } else if (!ev.shiftKey && document.activeElement === last) {
    first.focus();
    ev.preventDefault();
  }
}

function onPanelOpening() {
  previousFocus = document.activeElement as HTMLElement;
  stackIndex = ++sidePanelStackSeed;
  openSidePanelStack.push(stackIndex);
  if (openSidePanelStack.length === 1) {
    previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
  window.addEventListener('keydown', onKey);
}

async function onPanelOpened() {
  await nextTick();
  applyStackStyles();
  const target = panelRef.value?.querySelector<HTMLElement>('[data-autofocus]')
    ?? panelRef.value?.querySelector<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
    ?? panelRef.value;
  target?.focus();
}

function onPanelClosed() {
  removeFromStack();
  window.removeEventListener('keydown', onKey);
  previousFocus?.focus?.();
  previousFocus = null;
}

onBeforeUnmount(() => {
  removeFromStack();
  window.removeEventListener('keydown', onKey);
});

function applyStackStyles() {
  if (stackIndex === null) return;
  const overlayZIndex = SIDEPANEL_Z_INDEX_BASE + stackIndex * 2;
  if (overlayRef.value) overlayRef.value.style.zIndex = String(overlayZIndex);
  if (panelRef.value) panelRef.value.style.zIndex = String(overlayZIndex + 1);
}

function isTopSidePanel(): boolean {
  return stackIndex !== null && openSidePanelStack.at(-1) === stackIndex;
}

const sizePx = computed(() => ({ sm: '320px', md: '480px', lg: '640px', xl: '880px' }[props.size]));
const enterClass = computed(() => props.side === 'right' ? 'translate-x-full' : '-translate-x-full');
const titleId = computed(() => (props.title ? `ui-sidepanel-title-${sidePanelId}` : undefined));
const descriptionId = computed(() =>
  props.description ? `ui-sidepanel-desc-${sidePanelId}` : undefined
);
</script>

<template>
  <Teleport to="body">
    <transition
      enter-active-class="transition-opacity duration-base ease-enter"
      enter-from-class="opacity-0"
      leave-active-class="transition-opacity duration-fast ease-exit"
      leave-to-class="opacity-0"
    >
      <div
        v-if="modelValue"
        ref="overlayRef"
        class="ui-sidepanel__overlay fixed inset-0 z-overlay bg-bg-overlay"
        @vue:before-mount="onPanelOpening"
        @vue:mounted="onPanelOpened"
        @vue:before-unmount="onPanelClosed"
        @click.self="!staticBackdrop && close()"
      >
        <transition
          :enter-active-class="`transition-transform duration-base ease-enter`"
          :enter-from-class="enterClass"
          :leave-active-class="`transition-transform duration-fast ease-exit`"
          :leave-to-class="enterClass"
        >
          <aside
            v-if="modelValue"
            ref="panelRef"
            role="dialog"
            tabindex="-1"
            aria-modal="true"
            :aria-labelledby="titleId"
            :aria-describedby="descriptionId"
            :style="{ width: sizePx }"
            :class="[
              'ui-sidepanel fixed top-0 bottom-0 z-modal flex h-dvh max-h-dvh min-h-0 flex-col bg-bg-surface border-default shadow-lg max-w-full',
              side === 'right' ? 'right-0 border-l' : 'left-0 border-r',
            ]"
          >
            <header class="ui-sidepanel__header flex items-start justify-between gap-3 px-5 py-4 border-b border-subtle shrink-0">
              <div class="min-w-0">
                <slot
                  name="header"
                  :title-id="titleId"
                  :description-id="descriptionId"
                >
                  <h2
                    v-if="title"
                    :id="titleId"
                    class="t-h1 text-fg-strong"
                  >
                    {{ title }}
                  </h2>
                  <p
                    v-if="description"
                    :id="descriptionId"
                    class="text-sm text-fg-muted mt-1"
                  >
                    {{ description }}
                  </p>
                </slot>
              </div>
              <UiIconButton
                v-if="!hideClose"
                data-autofocus
                aria-label="Close panel"
                size="sm"
                variant="ghost"
                @click="close"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                ><path d="M18 6 6 18M6 6l12 12" /></svg>
              </UiIconButton>
            </header>
            <div
              :class="[
                'ui-sidepanel__body min-h-0 flex-1 px-5 py-4 overscroll-contain',
                scrollBody !== false && 'overflow-y-auto',
              ]"
            >
              <slot :close="close" />
            </div>
            <footer
              v-if="$slots.footer"
              class="ui-sidepanel__footer flex items-center justify-end gap-2 px-5 py-3 border-t border-subtle bg-bg-surface-alt shrink-0"
            >
              <slot
                name="footer"
                :close="close"
              />
            </footer>
          </aside>
        </transition>
      </div>
    </transition>
  </Teleport>
</template>
