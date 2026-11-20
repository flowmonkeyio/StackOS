<!--
  UiToast — single toast. Mount inside a fixed-position UiToastViewport.
  Toast container manages aria-live and stacking; this is the visual unit.

  In practice, use a `useToast()` composable to push toasts; this is the
  display primitive.
-->
<script setup lang="ts">
import { computed } from 'vue';
import UiIcon from './UiIcon.vue';

export type ToastTone = 'info' | 'success' | 'warning' | 'danger' | 'neutral';

export interface UiToastProps {
  tone?: ToastTone;
  title: string;
  description?: string;
  /** Optional action label (renders right-aligned button). */
  actionLabel?: string;
  /** Show dismiss button. */
  dismissible?: boolean;
}

const props = withDefaults(defineProps<UiToastProps>(), {
  tone: 'neutral',
  description: undefined,
  actionLabel: undefined,
  dismissible: true,
});

defineEmits<{
  (e: 'action'): void;
  (e: 'dismiss'): void;
}>();

const TOAST_TONE_STYLES = {
  info:    { icon: 'info',           iconClass: 'text-info',    barClass: 'bg-info' },
  success: { icon: 'check-circle',   iconClass: 'text-success', barClass: 'bg-success' },
  warning: { icon: 'alert-triangle', iconClass: 'text-warning', barClass: 'bg-warning' },
  danger:  { icon: 'x-circle',       iconClass: 'text-danger',  barClass: 'bg-danger' },
  neutral: { icon: 'info',           iconClass: 'text-neutral', barClass: 'bg-neutral' },
} as const;

const toneStyle = computed(() => TOAST_TONE_STYLES[props.tone]);
</script>

<template>
  <div
    :role="tone === 'danger' ? 'alert' : 'status'"
    :aria-live="tone === 'danger' ? 'assertive' : 'polite'"
    class="ui-toast pointer-events-auto relative w-full min-w-0 overflow-hidden rounded-lg border border-default bg-bg-surface p-3 pl-4 text-sm shadow-lg"
  >
    <span
      :class="['absolute inset-y-0 left-0 w-1', toneStyle.barClass]"
      aria-hidden="true"
    />
    <div class="flex items-start gap-2.5">
      <UiIcon
        :name="toneStyle.icon"
        :class="['mt-0.5 h-4 w-4 shrink-0', toneStyle.iconClass]"
        aria-hidden="true"
      />
      <div class="flex-1 min-w-0">
        <p
          class="break-words font-medium text-fg-strong"
          :title="title"
        >
          {{ title }}
        </p>
        <p
          v-if="description"
          class="mt-0.5 whitespace-pre-line break-words text-xs text-fg-muted"
        >
          {{ description }}
        </p>
        <button
          v-if="actionLabel"
          type="button"
          class="focus-ring mt-2 rounded-sm text-sm font-medium text-accent-fg hover:underline underline-offset-2"
          @click="$emit('action')"
        >
          {{ actionLabel }}
        </button>
      </div>
      <div class="flex items-center gap-1 shrink-0">
        <button
          v-if="dismissible"
          type="button"
          class="focus-ring -m-1 rounded-sm p-1 text-fg-subtle transition-colors duration-fast hover:text-fg-default"
          :aria-label="`Dismiss ${title}`"
          @click="$emit('dismiss')"
        >
          <UiIcon
            name="close"
            class="h-3.5 w-3.5"
            aria-hidden="true"
          />
        </button>
      </div>
    </div>
  </div>
</template>
