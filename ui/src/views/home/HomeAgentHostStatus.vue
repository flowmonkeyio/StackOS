<script setup lang="ts">
import { UiBadge, UiButton, UiCallout, UiSkeleton } from '@/components/ui'
import type { DesktopMcpHostStatus } from '@/lib/desktop'

import {
  agentHostLabel,
  agentHostLogo,
  agentHostPresentation,
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
      class="m-0 grid grid-cols-1 list-none gap-2 p-0 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6"
      aria-label="Checking AI tool connections"
      aria-hidden="true"
    >
      <li
        v-for="slot in 6"
        :key="slot"
        class="min-w-0 rounded-md border border-subtle bg-bg-surface-alt px-3 py-2"
      >
        <div class="flex h-6 items-center gap-2">
          <UiSkeleton shape="block" width="24px" height="24px" />
          <UiSkeleton shape="line" width="5rem" />
        </div>
        <div class="mt-1 flex h-4 items-center"><UiSkeleton shape="line" width="90%" /></div>
      </li>
    </ul>
    <UiCallout
      v-if="state.kind === 'error'"
      tone="warning"
      density="compact"
    >
      {{ state.message }}
      <span v-if="state.items.length"> Previously checked connections are shown.</span>
    </UiCallout>
    <ul
      v-if="state.items.length"
      class="m-0 grid grid-cols-1 list-none gap-2 p-0 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6"
    >
      <li
        v-for="host in state.items"
        :key="host.host_key"
        class="min-w-0 rounded-md border border-subtle bg-bg-surface-alt px-3 py-2"
      >
        <div class="flex min-w-0 items-center justify-between gap-2">
          <img
            :src="agentHostLogo(host)"
            :alt="agentHostLabel(host)"
            :title="agentHostLabel(host)"
            class="h-6 w-6 shrink-0 rounded-sm object-cover"
          >
          <UiBadge
            :tone="presentation(host).tone"
            size="sm"
            class="shrink-0 whitespace-nowrap"
          >
            {{ presentation(host).label }}
          </UiBadge>
        </div>
        <p
          class="mt-1 text-2xs text-fg-muted"
          :title="host.message"
        >
          <span class="block truncate">
            {{ presentation(host).detail }}
          </span>
        </p>
      </li>
    </ul>
  </section>
</template>
