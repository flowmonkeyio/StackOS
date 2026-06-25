<script setup lang="ts">
import { computed } from 'vue'

import { useDesktopUpdates } from '@/composables/useDesktopUpdates'
import UiIcon from '@/components/ui/UiIcon.vue'
import UiProgressBar from '@/components/ui/UiProgressBar.vue'

const {
  actionError,
  busy,
  canClick,
  percent,
  promptVisible,
  runPrimaryAction,
  status,
  version,
} = useDesktopUpdates()

const promptTitle = computed(() => {
  if (actionError.value) return 'Update needs attention'
  if (status.value === 'downloaded') return 'Update ready to install'
  if (status.value === 'downloading') return 'Downloading update'
  return version.value ? `StackOS ${version.value} is available` : 'StackOS update available'
})

const promptDetail = computed(() => {
  if (actionError.value) return actionError.value
  if (status.value === 'downloaded') return 'Click to install and restart StackOS.'
  if (status.value === 'downloading') {
    return percent.value === null ? 'Preparing the update.' : `${percent.value}% complete.`
  }
  return 'Click to download the update.'
})

const iconName = computed(() => {
  if (actionError.value) return 'alert-triangle'
  if (status.value === 'downloaded') return 'arrow-right'
  if (status.value === 'downloading') return 'loader'
  return 'save'
})
</script>

<template>
  <Transition name="desktop-update-prompt">
    <button
      v-if="promptVisible"
      type="button"
      :disabled="busy !== null || !canClick"
      :aria-busy="busy !== null || undefined"
      class="focus-ring fixed inset-x-4 bottom-4 z-toast mx-auto w-[calc(100vw-2rem)] max-w-md rounded-lg border border-info-border bg-bg-surface p-3 text-left text-sm shadow-lg transition-colors duration-fast hover:border-strong hover:bg-bg-surface-alt disabled:cursor-default disabled:hover:border-info-border disabled:hover:bg-bg-surface sm:left-auto sm:right-4 sm:mx-0"
      aria-live="polite"
      @click="runPrimaryAction"
    >
      <div class="flex items-start gap-3">
        <span class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
          <UiIcon
            :name="busy || status === 'downloading' ? 'loader' : iconName"
            :class="['h-4 w-4', (busy || status === 'downloading') && 'animate-spin']"
            aria-hidden="true"
          />
        </span>
        <span class="min-w-0 flex-1">
          <span class="block font-semibold text-fg-strong">
            {{ promptTitle }}
          </span>
          <span class="mt-0.5 block text-xs text-fg-muted">
            {{ promptDetail }}
          </span>
          <UiProgressBar
            v-if="status === 'downloading'"
            class="mt-2"
            :value="percent"
            tone="info"
            size="xs"
            aria-label="Update download progress"
          />
        </span>
        <UiIcon
          v-if="canClick && busy === null"
          name="arrow-right"
          class="mt-1 h-4 w-4 shrink-0 text-fg-subtle"
          aria-hidden="true"
        />
      </div>
    </button>
  </Transition>
</template>

<style scoped>
.desktop-update-prompt-enter-active,
.desktop-update-prompt-leave-active {
  transition:
    opacity var(--duration-base) var(--easing-standard),
    transform var(--duration-base) var(--easing-standard);
}

.desktop-update-prompt-enter-from,
.desktop-update-prompt-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
