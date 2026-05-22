<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaActionOut } from '@/api'
import { UiBadge, UiJsonBlock } from '@/components/ui'
import { sanitizeForDisplay } from '@/lib/stackos/json'

const props = withDefaults(defineProps<{
  action: SchemaActionOut
  open?: boolean
}>(), {
  open: false,
})

const inputSchema = computed(() => sanitizeForDisplay(props.action.input_schema_json))
const outputSchema = computed(() => sanitizeForDisplay(props.action.output_schema_json))
const config = computed(() => sanitizeForDisplay(props.action.config_json))
const availability = computed(() => props.action.availability)

const statusTone = computed(() => {
  switch (availability.value.status) {
    case 'ready':
      return 'success'
    case 'unknown':
    case 'missing_budget':
    case 'missing_credential':
      return 'warning'
    case 'budget_blocked':
    case 'credential_failed':
    case 'missing_connector':
    case 'plugin_disabled':
    case 'provider_disabled':
      return 'danger'
    default:
      return 'neutral'
  }
})

function humanize(value: string | null | undefined): string {
  if (!value) return '-'
  return value.replaceAll(/[-_.]/g, ' ')
}
</script>

<template>
  <details
    :open="open"
    class="group rounded-md border border-default bg-bg-surface shadow-xs"
    :aria-label="`${action.name} action schema`"
  >
    <summary class="grid cursor-pointer list-none gap-2 px-3 py-2 focus-ring sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center [&::-webkit-details-marker]:hidden">
      <div class="min-w-0">
        <div class="flex min-w-0 items-center gap-2">
          <span class="truncate text-sm font-semibold text-fg-strong">
            {{ action.name }}
          </span>
          <UiBadge>{{ action.key }}</UiBadge>
        </div>
        <p
          v-if="action.description"
          class="mt-0.5 truncate text-xs text-fg-muted"
        >
          {{ action.description }}
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-1.5 sm:justify-end">
        <UiBadge :tone="statusTone">
          {{ humanize(availability.status) }}
        </UiBadge>
        <UiBadge tone="accent">{{ action.plugin_slug }}</UiBadge>
        <UiBadge
          v-if="action.provider_key"
          tone="info"
        >
          {{ action.provider_key }}
        </UiBadge>
        <UiBadge :tone="action.risk_level === 'read' ? 'success' : 'warning'">
          {{ action.risk_level }}
        </UiBadge>
        <span
          class="i-lucide-chevron-down ml-1 h-4 w-4 text-fg-subtle transition-transform duration-fast group-open:rotate-180"
          aria-hidden="true"
        />
      </div>
    </summary>

    <div class="grid gap-3 border-t border-subtle p-3 text-sm md:grid-cols-2 xl:grid-cols-4">
      <div>
        <p class="text-xs font-semibold uppercase tracking-wide text-fg-muted">Action ref</p>
        <p class="mt-1 break-all font-mono text-xs text-fg-default">{{ action.action_ref }}</p>
      </div>
      <div>
        <p class="text-xs font-semibold uppercase tracking-wide text-fg-muted">Connector</p>
        <div class="mt-1 flex flex-wrap gap-1.5">
          <UiBadge :tone="availability.connector_registered ? 'success' : 'danger'">
            {{ action.connector_key ?? 'none' }}
          </UiBadge>
          <UiBadge variant="outline">{{ action.operation }}</UiBadge>
        </div>
      </div>
      <div>
        <p class="text-xs font-semibold uppercase tracking-wide text-fg-muted">Credential</p>
        <div class="mt-1 flex flex-wrap gap-1.5">
          <UiBadge :tone="availability.requires_credential && availability.credential_state !== 'available' ? 'warning' : 'neutral'">
            {{ humanize(availability.credential_state) }}
          </UiBadge>
          <UiBadge
            v-if="availability.credential_refs?.length"
            tone="info"
          >
            {{ availability.credential_refs.length }}
          </UiBadge>
        </div>
      </div>
      <div>
        <p class="text-xs font-semibold uppercase tracking-wide text-fg-muted">Budget</p>
        <div class="mt-1 flex flex-wrap gap-1.5">
          <UiBadge :tone="availability.budget_state === 'blocked' ? 'danger' : availability.budget_state === 'missing' ? 'warning' : 'neutral'">
            {{ humanize(availability.budget_state) }}
          </UiBadge>
          <UiBadge
            v-if="availability.budget_kind"
            variant="outline"
          >
            {{ availability.budget_kind }}
          </UiBadge>
        </div>
      </div>
      <div
        v-if="availability.reasons?.length"
        class="md:col-span-2 xl:col-span-4"
      >
        <p class="text-xs font-semibold uppercase tracking-wide text-fg-muted">Reasons</p>
        <div class="mt-1 flex flex-wrap gap-1.5">
          <UiBadge
            v-for="reason in availability.reasons"
            :key="reason"
            tone="warning"
            variant="outline"
          >
            {{ humanize(reason) }}
          </UiBadge>
        </div>
      </div>
    </div>

    <div class="grid gap-3 border-t border-subtle p-3 lg:grid-cols-2">
      <div>
        <h4 class="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Input
        </h4>
        <UiJsonBlock
          :data="inputSchema"
          density="compact"
          max-height="18rem"
          wrap
        />
      </div>
      <div>
        <h4 class="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Output
        </h4>
        <UiJsonBlock
          :data="outputSchema"
          density="compact"
          max-height="18rem"
          wrap
        />
      </div>
    </div>

    <details
      v-if="config"
      class="mx-3 mb-3 rounded-md border border-subtle bg-bg-surface"
    >
      <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default focus-ring">
        Connector config
      </summary>
      <div class="border-t border-subtle p-3">
        <UiJsonBlock
          :data="config"
          density="compact"
          max-height="14rem"
          wrap
        />
      </div>
    </details>
  </details>
</template>
