<script setup lang="ts">
import { UiBadge, UiButton, UiCallout } from '@/components/ui'
import type { DesktopMcpHostStatus } from '@/lib/desktop'

import {
  agentHostLabel,
  agentHostPresentation,
  SUPPORTED_AGENT_HOSTS,
} from './agentHostPresentation'
import type { HostStatusState } from './useHomeAgentHostStatuses'

defineProps<{
  state: HostStatusState
  summary: string
}>()

defineEmits<{
  refresh: []
}>()

function presentation(host: DesktopMcpHostStatus) {
  return agentHostPresentation(host)
}
</script>

<template>
  <section
    class="border-t border-subtle pt-3"
    aria-label="AI tool connections"
  >
    <div class="mb-2 flex items-center justify-between gap-3">
      <div class="min-w-0">
        <h3 class="text-xs font-medium text-fg-muted">
          AI tool connections
        </h3>
        <p class="text-2xs text-fg-subtle">
          {{ summary }}
        </p>
      </div>
      <UiButton
        variant="ghost"
        size="sm"
        icon-left="refresh"
        :loading="state.kind === 'loading'"
        @click="$emit('refresh')"
      >
        Refresh
      </UiButton>
    </div>

    <ul
      v-if="state.kind === 'loading' && state.items.length === 0"
      class="m-0 grid list-none gap-2 p-0 sm:grid-cols-2 lg:grid-cols-4"
    >
      <li
        v-for="host in SUPPORTED_AGENT_HOSTS"
        :key="host.host_key"
        class="min-w-0 rounded-md border border-subtle bg-bg-surface-alt px-3 py-2"
      >
        <div class="flex min-w-0 flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span class="text-sm font-medium text-fg-strong">{{ host.label }}</span>
          <UiBadge
            tone="neutral"
            size="sm"
          >
            Checking
          </UiBadge>
        </div>
        <p class="mt-1 text-2xs text-fg-muted">
          Checking the StackOS connection…
        </p>
      </li>
    </ul>
    <UiCallout
      v-else-if="state.kind === 'error'"
      tone="warning"
      density="compact"
    >
      {{ state.message }}
    </UiCallout>
    <ul
      v-else
      class="m-0 grid list-none gap-2 p-0 sm:grid-cols-2 lg:grid-cols-4"
    >
      <li
        v-for="host in state.items"
        :key="host.host_key"
        class="min-w-0 rounded-md border border-subtle bg-bg-surface-alt px-3 py-2"
      >
        <div class="flex min-w-0 flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span class="text-sm font-medium text-fg-strong">
            {{ agentHostLabel(host.host_key) }}
          </span>
          <UiBadge
            :tone="presentation(host).tone"
            size="sm"
          >
            {{ presentation(host).label }}
          </UiBadge>
        </div>
        <p
          class="mt-1 text-2xs text-fg-muted"
          :title="host.message"
        >
          {{ presentation(host).detail }}
        </p>
      </li>
    </ul>
  </section>
</template>
