<script setup lang="ts">
import { computed } from 'vue'

import { UiButton, UiCallout } from '@/components/ui'
import type { TelegramAccountSessionStatus } from '@/stores/plugins'

const props = withDefaults(
  defineProps<{
    state: TelegramAccountSessionStatus | null
    busy: boolean
    dirty?: boolean
  }>(),
  { dirty: false },
)

const emit = defineEmits<{
  connect: []
  disconnect: []
  refresh: []
}>()

function connect(): void {
  if (!props.dirty) emit('connect')
}

function disconnect(): void {
  emit('disconnect')
}

const canDisconnect = computed(() => Boolean(props.state?.connected || props.state?.desired_connected))

const statusLabel = computed(() => {
  if (!props.state) return 'Checking session'
  if (props.state.connected) return 'Connected'
  if (props.state.status === 'authorization_required') return 'Sign-in required'
  if (props.state.desired_connected) return 'Reconnect needed'
  return 'Disconnected'
})

const statusTone = computed(() => {
  if (!props.state) return 'info'
  if (props.state.connected) return 'success'
  return props.state.desired_connected ? 'warning' : 'info'
})
</script>

<template>
  <section class="grid gap-3 border-t border-border-subtle pt-4" aria-label="Telegram session">
    <div class="grid gap-1">
      <h3 class="text-sm font-semibold text-fg-strong">Telegram session</h3>
      <p class="text-sm text-fg-muted">
        Control this Account's shared TDLib connection. Disconnect stops TDLib but preserves saved
        authorization; a later connect normally resumes it. Telegram asks a user to sign in again
        only if it invalidates that authorization.
      </p>
    </div>

    <UiCallout :tone="statusTone" density="compact" :title="statusLabel">
      <template v-if="state">
        {{ state.next_action || 'The session has no additional action to take.' }}
        <p v-if="state.affects_other_projects" class="mt-2">
          This Account is attached to other projects. Connecting or disconnecting changes their
          shared session too.
        </p>
        <p v-if="state.status === 'authorization_required'" class="mt-2">
          This Account still needs local user sign-in. Open Accounts and complete the displayed phone,
          code, or password flow; the original connection request remains in effect.
        </p>
        <p v-if="state.desired_connected" class="mt-2">
          To change Telegram, API, or proxy settings, disconnect first, save the changes, then
          connect again. You can rename the Account while it remains connected.
        </p>
      </template>
      <template v-else>Refresh to load the current shared session state.</template>
    </UiCallout>

    <div class="flex flex-wrap gap-2">
      <UiButton
        v-if="!canDisconnect"
        size="sm"
        variant="primary"
        :loading="busy"
        :disabled="busy || dirty"
        @click="connect"
      >
        Connect session
      </UiButton>
      <UiButton
        v-else
        size="sm"
        variant="secondary"
        :loading="busy"
        :disabled="busy"
        @click="disconnect"
      >
        Disconnect session
      </UiButton>
      <UiButton size="sm" variant="ghost" :loading="busy" @click="$emit('refresh')">
        Refresh status
      </UiButton>
    </div>
  </section>
</template>
