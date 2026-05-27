<script setup lang="ts">
// AuthErrorView — shown when bootstrap or any subsequent /api/v1/* call
// returns 401, or when the daemon is unreachable.

import { computed } from 'vue'
import { storeToRefs } from 'pinia'

import {
  UiButton,
  UiEmptyState,
  UiPageShell,
} from '@/components/ui'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const { state } = storeToRefs(authStore)

const errorMessage = computed<string>(() => {
  if (state.value.kind === 'error') {
    return state.value.message
  }
  return 'Authentication required.'
})

const errorStatus = computed<number | null>(() => {
  if (state.value.kind === 'error' && typeof state.value.status === 'number') {
    return state.value.status
  }
  return null
})

async function retry(): Promise<void> {
  await authStore.bootstrap()
  if (authStore.ready) {
    window.location.assign('/')
  }
}
</script>

<template>
  <UiPageShell class="flex min-h-[60vh] items-center justify-center">
    <UiEmptyState
      title="Daemon unreachable"
      :description="`${errorMessage}${errorStatus !== null ? ` (HTTP ${errorStatus})` : ''}`"
      size="lg"
    >
      <template #actions>
        <UiButton
          variant="primary"
          @click="retry"
        >
          Retry
        </UiButton>
      </template>
    </UiEmptyState>
  </UiPageShell>
</template>
