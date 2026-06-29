<script setup lang="ts">
import { UiBadge, UiButton, UiCallout, UiSkeleton } from '@/components/ui'
import type { DesktopMcpHostStatus } from '@/lib/desktop'

import type { HostStatusState } from './useHomeAgentHostStatuses'

defineProps<{
  state: HostStatusState
  summary: string
}>()

defineEmits<{
  refresh: []
}>()

const HOST_LABELS: Record<string, string> = {
  codex: 'Codex',
  'claude-code': 'Claude Code',
  'claude-desktop': 'Claude Desktop',
  'gemini-cli': 'Gemini CLI',
}

function hostLabel(host: DesktopMcpHostStatus): string {
  return HOST_LABELS[host.host_key] ?? host.host_key
}

function hostTone(host: DesktopMcpHostStatus): 'success' | 'warning' | 'danger' | 'neutral' {
  if (host.needs_restart) return 'warning'
  if (host.status === 'registered_unsafe' || host.status === 'config_unreadable') return 'danger'
  if (host.status === 'unsupported_host_version') return 'neutral'
  if (host.status === 'available_unregistered') return 'warning'
  if (host.blocking || host.ok === false) return 'warning'
  if (host.ok && host.available) return 'success'
  return 'neutral'
}

function hostStateLabel(host: DesktopMcpHostStatus): string {
  if (host.needs_restart) return 'Restart required'
  if (host.status === 'absent' || host.available === false) return 'Not installed'
  if (host.status === 'available_unregistered') return 'Not connected'
  if (host.status === 'registered_stale') return 'Repair'
  if (host.status === 'registered_unsafe') return 'Unsafe'
  if (host.status === 'config_unreadable') return 'Config issue'
  if (host.status === 'unsupported_host_version') return 'Unsupported'
  if (host.ok && host.available) return 'Connected'
  return 'Check status'
}
</script>

<template>
  <section
    class="border-t border-subtle pt-3"
    aria-label="Agent host connections"
  >
    <div class="mb-2 flex items-center justify-between gap-3">
      <div class="min-w-0">
        <h3 class="text-xs font-medium text-fg-muted">
          Agent hosts
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

    <div
      v-if="state.kind === 'loading' && state.items.length === 0"
      class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4"
    >
      <UiSkeleton
        v-for="n in 4"
        :key="n"
        shape="block"
        height="3.75rem"
      />
    </div>
    <UiCallout
      v-else-if="state.kind === 'error'"
      tone="warning"
      density="compact"
    >
      {{ state.message }}
    </UiCallout>
    <ul
      v-else
      class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4"
    >
      <li
        v-for="host in state.items"
        :key="host.host_key"
        class="min-w-0 rounded-md border border-subtle bg-bg-surface-alt px-3 py-2"
      >
        <div class="flex min-w-0 items-center justify-between gap-2">
          <span class="truncate text-sm font-medium text-fg-strong">
            {{ hostLabel(host) }}
          </span>
          <UiBadge
            :tone="hostTone(host)"
            size="sm"
          >
            {{ hostStateLabel(host) }}
          </UiBadge>
        </div>
        <p
          v-if="host.message"
          class="mt-1 truncate text-2xs text-fg-muted"
        >
          {{ host.message }}
        </p>
      </li>
    </ul>
  </section>
</template>
