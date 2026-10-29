<!--
  UiDialog — modal dialog. Centered, focus-trapped, scroll-locked.

  Always:
    - title (accessible name)
    - explicit close button
    - footer with primary / secondary actions

  For yes/no confirmations, use UiConfirmDialog.
-->
<script lang="ts">
let dialogIdSeed = 0;
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue';
import UiIcon from './UiIcon.vue';
import UiIconButton from './UiIconButton.vue';
import { closeOverlay, isTopOverlay, openOverlay, type OverlayEntry } from './overlayStack';

export interface UiDialogProps {
  modelValue: boolean;
  title?: string;
  description?: string;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  /** Hide the X. Use only when dismiss requires explicit user action. */
  hideClose?: boolean;
  /** Disable backdrop click-to-close. */
  staticBackdrop?: boolean;
  /** Disable Escape-to-close. */
  noEscape?: boolean;
  /** Wider, scrollable body for forms. */
  scrollBody?: boolean;
  /** Stable viewport-bounded frame for reading and editing detail content. */
  layout?: 'default' | 'detail';
}

const props = withDefaults(defineProps<UiDialogProps>(), {
  title: undefined,
  description: undefined,
  size: 'md',
  layout: 'default',
});

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'close'): void;
}>();

const dialogId = ++dialogIdSeed;
const dialogRef = ref<HTMLDivElement | null>(null);
const overlayRef = ref<HTMLDivElement | null>(null);
const bodyRef = ref<HTMLDivElement | null>(null);
let overlayEntry: OverlayEntry | null = null;

function close() {
  emit('update:modelValue', false);
  emit('close');
}

function removeFromStack() {
  closeOverlay(overlayEntry);
  overlayEntry = null;
}

function onKey(ev: KeyboardEvent) {
  if (!props.modelValue) return;
  if (!isTopOverlay(overlayEntry)) return;
  if (ev.key === 'Escape' && !props.noEscape) {
    ev.preventDefault();
    close();
    ev.stopImmediatePropagation();
    ev.stopPropagation();
  } else if (ev.key === 'Tab') {
    trapFocus(ev);
  }
}

function trapFocus(ev: KeyboardEvent) {
  const node = dialogRef.value;
  if (!node) return;
  const focusables = Array.from(node.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
  )).filter((element) => {
    if (element.closest('[hidden], [inert]')) return false;
    for (let parent = element.parentElement; parent && parent !== node; parent = parent.parentElement) {
      if (parent instanceof HTMLDetailsElement && !parent.open && !parent.querySelector('summary')?.contains(element)) return false;
    }
    return true;
  });
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (ev.shiftKey && (document.activeElement === first || !focusables.includes(document.activeElement as HTMLElement))) {
    last.focus();
    ev.preventDefault();
  } else if (!ev.shiftKey && document.activeElement === last) {
    first.focus();
    ev.preventDefault();
  }
}

function onDialogOpening() {
  overlayEntry ??= openOverlay(() => dialogRef.value);
  window.addEventListener('keydown', onKey);
}

async function onDialogOpened() {
  applyStackStyles();
  await nextTick();
  if (!isTopOverlay(overlayEntry)) return;
  const target = dialogRef.value?.querySelector<HTMLElement>('[data-autofocus]')
    ?? dialogRef.value?.querySelector<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
  target?.focus();
}

function onDialogClosed() {
  removeFromStack();
  window.removeEventListener('keydown', onKey);
}

onBeforeUnmount(() => {
  removeFromStack();
  window.removeEventListener('keydown', onKey);
});

const sizeClass = computed(() => ({
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
}[props.size]));

const titleId = computed(() => (props.title ? `ui-dialog-title-${dialogId}` : undefined));
const descriptionId = computed(() =>
  props.description ? `ui-dialog-desc-${dialogId}` : undefined
);
function applyStackStyles() {
  if (!overlayEntry) return;
  if (overlayRef.value) overlayRef.value.style.zIndex = String(overlayEntry.layer);
  if (dialogRef.value) dialogRef.value.style.zIndex = String(overlayEntry.layer + 1);
}

function focusTitle() {
  dialogRef.value?.querySelector<HTMLElement>('.ui-dialog__title')?.focus({ preventScroll: true });
}
defineExpose({ body: bodyRef, focusTitle });
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
        class="ui-dialog__overlay fixed inset-0 z-overlay bg-bg-overlay backdrop-blur-[2px] flex items-center justify-center p-4"
        @vue:before-mount="onDialogOpening"
        @vue:mounted="onDialogOpened"
        @vue:before-unmount="onDialogClosed"
        @click.self="!staticBackdrop && close()"
      >
        <transition
          enter-active-class="transition duration-base ease-enter"
          enter-from-class="opacity-0 scale-[0.98]"
          leave-active-class="transition duration-fast ease-exit"
          leave-to-class="opacity-0 scale-[0.98]"
        >
          <div
            v-if="modelValue"
            ref="dialogRef"
            role="dialog"
            aria-modal="true"
            :aria-labelledby="titleId"
            :aria-describedby="descriptionId"
            :class="[
              'ui-dialog z-modal w-full rounded-xl border border-default bg-bg-surface shadow-xl flex flex-col max-h-[calc(100vh-2rem)]',
              sizeClass,
              layout === 'detail' && 'ui-dialog--detail',
            ]"
          >
            <header
              v-if="title || $slots.header || !hideClose"
              class="ui-dialog__header flex items-start justify-between gap-3 px-5 py-4 border-b border-subtle"
            >
              <div class="min-w-0">
                <slot name="header">
                  <h2
                    v-if="title"
                    :id="titleId"
                    class="ui-dialog__title t-h2 text-fg-strong"
                    tabindex="-1"
                    :title="title"
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
                aria-label="Close dialog"
                size="md"
                class="shrink-0"
                variant="ghost"
                @click="close"
              >
                <UiIcon
                  name="close"
                  class="h-4 w-4"
                  aria-hidden="true"
                />
              </UiIconButton>
            </header>
            <div
              ref="bodyRef"
              :class="[
                'ui-dialog__body px-5 py-4',
                scrollBody && 'overflow-y-auto',
              ]"
            >
              <slot />
            </div>
            <footer
              v-if="$slots.footer"
              class="ui-dialog__footer flex items-center justify-end gap-2 px-5 py-3 border-t border-subtle bg-bg-surface-alt rounded-b-xl"
            >
              <slot
                name="footer"
                :close="close"
              />
            </footer>
          </div>
        </transition>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.ui-dialog--detail {
  height: min(880px, calc(100dvh - 48px));
  max-height: min(880px, calc(100dvh - 48px));
  min-width: 0;
  overflow: hidden;
}
.ui-dialog--detail .ui-dialog__header,
.ui-dialog--detail .ui-dialog__footer {
  flex: none;
}
.ui-dialog--detail .ui-dialog__header {
  min-height: 5rem;
}
.ui-dialog--detail .ui-dialog__title {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.ui-dialog--detail .ui-dialog__header p {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ui-dialog--detail .ui-dialog__body {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  overflow-wrap: anywhere;
}
.ui-dialog--detail .ui-dialog__footer {
  min-height: 3.5rem;
  flex-wrap: wrap;
}
</style>
