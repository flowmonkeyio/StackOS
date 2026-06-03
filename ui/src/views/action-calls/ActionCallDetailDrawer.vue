<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaActionCallAuditOut } from '@/api'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiCallout,
  UiFactGroups,
  UiJsonBlock,
  UiSidePanel,
} from '@/components/ui'
import type { UiFactGroup } from '@/components/ui/UiFactGroups.vue'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'

const props = defineProps<{
  modelValue: boolean
  call: SchemaActionCallAuditOut | null
}>()

defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const title = computed(() => (props.call ? `Action Call #${props.call.id}` : 'Action Call'))
const description = computed(() => (props.call ? callTitle(props.call) : undefined))

const summaryGroups = computed<UiFactGroup[]>(() => {
  const call = props.call
  if (!call) return []
  const hasRunContext = Boolean(call.run_id || call.run_plan_id || call.run_plan_step_id)
  return [
    {
      title: 'Execution Target',
      description: 'Provider, connector, and operation resolved by StackOS.',
      items: [
        { label: 'Provider', value: call.provider_key ?? null, emphasis: 'strong' },
        { label: 'Connector', value: call.connector_key ?? null },
        { label: 'Operation', value: call.operation, mono: true, wide: true },
        { label: 'Credential', value: call.credential_ref ?? null, mono: true, wide: true },
      ],
    },
    {
      title: 'Run Context',
      description: hasRunContext
        ? 'Workflow/run attachment for this audited call.'
        : 'Direct action call; not attached to a run.',
      items: [
        { label: 'Run', value: call.run_id ? `#${call.run_id}` : null },
        { label: 'Run plan', value: call.run_plan_id ? `#${call.run_plan_id}` : null },
        { label: 'Step', value: call.run_plan_step_id ? `#${call.run_plan_step_id}` : null },
      ],
    },
    {
      title: 'Outcome',
      description: call.dry_run ? 'Validation-only execution.' : 'Recorded provider execution result.',
      items: [
        { label: 'Cost', value: `${call.cost_cents} cents` },
        { label: 'Duration', value: formatDuration(call.duration_ms) },
        {
          label: 'Dry run',
          value: call.dry_run,
          badge: true,
          tone: call.dry_run ? 'warning' : 'neutral',
        },
      ],
    },
    {
      title: 'Timeline',
      items: [
        { label: 'Created', value: formatDateTime(call.created_at), wide: true },
        {
          label: 'Completed',
          value: call.completed_at ? formatDateTime(call.completed_at) : null,
          wide: true,
        },
      ],
    },
  ]
})

function callTitle(call: SchemaActionCallAuditOut): string {
  return `${call.plugin_slug}:${call.action_key}`
}

function formatDuration(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : `${value}ms`
}
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    :title="title"
    :description="description"
    size="lg"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <template #header>
      <div
        v-if="call"
        class="min-w-0"
      >
        <div class="flex flex-wrap items-center gap-2">
          <h2
            id="ui-sidepanel-title"
            class="t-h1 text-fg-strong"
          >
            Action Call #{{ call.id }}
          </h2>
          <StatusBadge
            :status="call.status"
            kind="job"
            :small="true"
          />
        </div>
        <p class="mt-1 truncate font-mono text-xs text-fg-muted">
          {{ callTitle(call) }}
        </p>
      </div>
    </template>

    <div
      v-if="call"
      class="grid gap-4"
    >
      <UiCallout
        v-if="call.error"
        tone="danger"
        density="compact"
      >
        {{ call.error }}
      </UiCallout>

      <UiFactGroups
        :groups="summaryGroups"
        density="compact"
        aria-label="Action call summary"
      />

      <section class="grid gap-2 border-t border-subtle pt-4">
        <h3 class="text-xs font-medium uppercase text-fg-subtle">Request</h3>
        <UiJsonBlock
          :data="sanitizeForDisplay(call.request_json ?? {})"
          density="compact"
          max-height="14rem"
          wrap
          aria-label="Action call request"
        />
      </section>

      <section class="grid gap-2 border-t border-subtle pt-4">
        <h3 class="text-xs font-medium uppercase text-fg-subtle">Provider Context</h3>
        <UiJsonBlock
          :data="sanitizeForDisplay(call.provider_context_json ?? {})"
          density="compact"
          max-height="10rem"
          wrap
          aria-label="Action call provider context"
        />
      </section>

      <section class="grid gap-2 border-t border-subtle pt-4">
        <h3 class="text-xs font-medium uppercase text-fg-subtle">Response</h3>
        <UiJsonBlock
          :data="sanitizeForDisplay(call.response_json ?? {})"
          density="compact"
          max-height="16rem"
          wrap
          aria-label="Action call response"
        />
      </section>

      <section class="grid gap-2 border-t border-subtle pt-4">
        <h3 class="text-xs font-medium uppercase text-fg-subtle">Metadata</h3>
        <UiJsonBlock
          :data="sanitizeForDisplay(call.metadata_json ?? {})"
          density="compact"
          max-height="12rem"
          wrap
          aria-label="Action call metadata"
        />
      </section>
    </div>
  </UiSidePanel>
</template>
