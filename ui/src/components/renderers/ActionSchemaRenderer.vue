<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaActionOut } from '@/api'
import { UiAdvancedJsonPanel, UiBadge, UiCard, UiIcon } from '@/components/ui'
import { sanitizeForDisplay } from '@/lib/stackos/json'

const props = withDefaults(
  defineProps<{
    action: SchemaActionOut
    open?: boolean
  }>(),
  {
    open: false,
  },
)

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
  <UiCard
    section
    :padded="false"
    class="overflow-hidden"
    :aria-label="`${action.name} action schema`"
  >
    <template #header>
      <div class="min-w-0 flex-1">
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <h3 class="t-h3 truncate text-fg-strong">
            {{ action.name }}
          </h3>
          <span class="font-mono text-2xs text-fg-subtle">{{ action.key }}</span>
        </div>
        <p
          v-if="action.description"
          class="mt-0.5 truncate text-xs text-fg-muted"
        >
          {{ action.description }}
        </p>
      </div>
      <div class="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
        <UiBadge :tone="statusTone">
          {{ humanize(availability.status) }}
        </UiBadge>
        <UiBadge tone="accent">
          {{ action.plugin_slug }}
        </UiBadge>
        <UiBadge
          v-if="action.provider_key"
          tone="info"
        >
          {{ action.provider_key }}
        </UiBadge>
        <UiBadge :tone="action.risk_level === 'read' ? 'success' : 'warning'">
          {{ action.risk_level }}
        </UiBadge>
      </div>
    </template>

    <details
      class="group/action"
      :open="open"
    >
      <summary
        class="focus-ring flex cursor-pointer list-none items-center gap-1.5 px-4 py-3 text-2xs font-medium text-fg-muted transition-colors duration-fast hover:text-fg-default [&::-webkit-details-marker]:hidden"
      >
        <UiIcon
          name="chevron-right"
          class="ui-action-schema-renderer__chevron h-3 w-3 shrink-0 text-fg-subtle transition-transform duration-fast group-open/action:rotate-90"
          aria-hidden="true"
        />
        Schema &amp; availability
      </summary>

      <div class="grid gap-3 border-t border-subtle px-4 py-3 md:grid-cols-2 xl:grid-cols-4">
        <div class="min-w-0">
          <p class="text-2xs font-medium text-fg-muted">
            Action ref
          </p>
          <p class="mt-1 break-all font-mono text-xs text-fg-default">
            {{ action.action_ref }}
          </p>
        </div>
        <div class="min-w-0">
          <p class="text-2xs font-medium text-fg-muted">
            Connector
          </p>
          <div class="mt-1 flex flex-wrap gap-1.5">
            <UiBadge :tone="availability.connector_registered ? 'success' : 'danger'">
              {{ action.connector_key ?? 'none' }}
            </UiBadge>
            <UiBadge variant="outline">
              {{ action.operation }}
            </UiBadge>
          </div>
        </div>
        <div class="min-w-0">
          <p class="text-2xs font-medium text-fg-muted">
            Credential
          </p>
          <div class="mt-1 flex flex-wrap gap-1.5">
            <UiBadge
              :tone="
                availability.requires_credential && availability.credential_state !== 'available'
                  ? 'warning'
                  : 'neutral'
              "
            >
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
        <div class="min-w-0">
          <p class="text-2xs font-medium text-fg-muted">
            Budget
          </p>
          <div class="mt-1 flex flex-wrap gap-1.5">
            <UiBadge
              :tone="
                availability.budget_state === 'blocked'
                  ? 'danger'
                  : availability.budget_state === 'missing'
                    ? 'warning'
                    : 'neutral'
              "
            >
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
          <p class="text-2xs font-medium text-fg-muted">
            Reasons
          </p>
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

      <div class="space-y-2 border-t border-subtle px-4 py-3">
        <UiAdvancedJsonPanel
          title="Input schema"
          summary="Raw JSON"
          :data="inputSchema"
          max-height="18rem"
        />
        <UiAdvancedJsonPanel
          title="Output schema"
          summary="Raw JSON"
          :data="outputSchema"
          max-height="18rem"
        />
        <UiAdvancedJsonPanel
          v-if="config"
          title="Connector config"
          summary="Raw JSON"
          :data="config"
          max-height="14rem"
        />
      </div>
    </details>
  </UiCard>
</template>
