<!--
  UiSidePanel — slide-in panel anchored to viewport edge.
  Use for: detail views (e.g. clicking a row in a list), filters,
  multi-step setup. NOT for navigation menus on desktop.
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
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
});

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'close'): void;
}>();

const panelRef = ref<HTMLElement | null>(null);

function close() {
  emit('update:modelValue', false);
  emit('close');
}

function onKey(ev: KeyboardEvent) {
  if (props.modelValue && ev.key === 'Escape') {
    close();
    ev.stopPropagation();
  }
}

watch(() => props.modelValue, (v) => {
  if (v) {
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', onKey);
  } else {
    document.body.style.overflow = '';
    window.removeEventListener('keydown', onKey);
  }
});

onBeforeUnmount(() => {
  document.body.style.overflow = '';
  window.removeEventListener('keydown', onKey);
});

const sizePx = computed(() => ({ sm: '320px', md: '480px', lg: '640px', xl: '880px' }[props.size]));
const enterClass = computed(() => props.side === 'right' ? 'translate-x-full' : '-translate-x-full');
</script>

<template>
  <transition
    enter-active-class="transition-opacity duration-base ease-enter"
    enter-from-class="opacity-0"
    leave-active-class="transition-opacity duration-fast ease-exit"
    leave-to-class="opacity-0"
  >
    <div
      v-if="modelValue"
      class="ui-sidepanel__overlay fixed inset-0 z-overlay bg-bg-overlay"
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
          aria-modal="true"
          :aria-labelledby="title ? 'ui-sidepanel-title' : undefined"
          :class="[
            'ui-sidepanel fixed top-0 bottom-0 z-modal flex min-h-0 flex-col bg-bg-surface border-default shadow-lg max-w-full',
            side === 'right' ? 'right-0 border-l' : 'left-0 border-r',
          ]"
          :style="{ width: sizePx }"
        >
          <header class="ui-sidepanel__header flex items-start justify-between gap-3 px-5 py-4 border-b border-subtle shrink-0">
            <div class="min-w-0">
              <slot name="header">
                <h2
                  v-if="title"
                  id="ui-sidepanel-title"
                  class="t-h1 text-fg-strong"
                >
                  {{ title }}
                </h2>
                <p
                  v-if="description"
                  class="text-sm text-fg-muted mt-1"
                >
                  {{ description }}
                </p>
              </slot>
            </div>
            <UiIconButton
              v-if="!hideClose"
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
              'ui-sidepanel__body min-h-0 flex-1 px-5 py-4',
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
</template>
