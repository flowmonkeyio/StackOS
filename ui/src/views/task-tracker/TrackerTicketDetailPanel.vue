<script setup lang="ts">
import { computed } from 'vue'

import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import {
  UiBadge,
  UiCallout,
  UiJsonBlock,
  UiMetadataStrip,
} from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'
import type { TrackerTicket } from '@/lib/task-tracker/types'

import { hasJsonObject } from './detailUtils'

const props = defineProps<{
  modelValue: boolean
  ticket: TrackerTicket | null
  title: string
  description?: string
}>()

defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const ticketFacts = computed(() => {
  const ticket = props.ticket
  if (!ticket) return []
  return [
    { label: 'Task', value: ticket.task_key, mono: true },
    { label: 'Assignee', value: ticket.assignee ?? '-' },
    { label: 'Priority', value: ticket.priority_key, mono: true },
    { label: 'Lane', value: ticket.lane_key, mono: true },
    { label: 'Kind', value: ticket.kind, mono: true },
    { label: 'Source', value: ticket.source_kind, mono: true },
    { label: 'Updated', value: formatDateTime(ticket.updated_at) },
  ]
})

const ticketTraceFacts = computed(() => {
  const ticket = props.ticket
  if (!ticket) return []
  return [
    { label: 'Run plan', value: ticket.run_plan_id ?? '-' },
    { label: 'Run', value: ticket.run_id ?? '-' },
    { label: 'Step', value: ticket.run_plan_step_id ?? '-' },
    { label: 'Parent', value: ticket.parent_ticket_key ?? '-' },
    { label: 'Created', value: formatDateTime(ticket.created_at) },
    { label: 'Claimed', value: ticket.claimed_at ? formatDateTime(ticket.claimed_at) : '-' },
    { label: 'Started', value: ticket.started_at ? formatDateTime(ticket.started_at) : '-' },
    { label: 'Completed', value: ticket.completed_at ? formatDateTime(ticket.completed_at) : '-' },
  ]
})

const hasTicketJson = computed(() => {
  const ticket = props.ticket
  if (!ticket) return false
  return [
    ticket.completion_evidence_json,
    ticket.source_json,
    ticket.context_json,
    ticket.metadata_json,
  ].some(hasJsonObject)
})

const isWorkflowLinked = computed(() => {
  const ticket = props.ticket
  if (!ticket) return false
  return Boolean(ticket.run_plan_id || ticket.run_plan_step_id || ticket.run_id)
})

function relationSummary(ticket: TrackerTicket): string {
  const parts = []
  if (ticket.dependency_keys.length) parts.push(`${ticket.dependency_keys.length} dependencies`)
  if (ticket.blocked_by.length) parts.push(`${ticket.blocked_by.length} blockers`)
  if (ticket.reference_count) parts.push(`${ticket.reference_count} refs`)
  if (ticket.link_count) parts.push(`${ticket.link_count} links`)
  return parts.join(' · ') || 'No relationships recorded'
}
</script>

<template>
  <InspectableDetailDrawer
    :model-value="modelValue"
    :title="title"
    :description="description"
    size="lg"
    :has-detail="Boolean(ticket)"
    empty-title="No ticket selected"
    empty-description="Pick a ticket from the graph or table."
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div
      v-if="ticket"
      class="space-y-5"
    >
      <section class="space-y-3 rounded-lg border border-subtle bg-bg-surface-alt px-3 py-3">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 class="t-h3 text-fg-strong">
              Overview
            </h3>
            <div class="mt-1.5 flex flex-wrap items-center gap-2">
              <StatusBadge
                domain="tracker"
                :status="ticket.status"
              />
              <UiBadge
                v-if="isWorkflowLinked"
                tone="accent"
              >
                Run plan linked
              </UiBadge>
            </div>
          </div>
          <div class="min-w-0 text-right">
            <p class="text-xs font-medium text-fg-muted">
              Relationships
            </p>
            <p class="mt-1.5 text-sm text-fg-default">
              {{ relationSummary(ticket) }}
            </p>
          </div>
        </div>
        <UiMetadataStrip
          :items="ticketFacts"
          aria-label="Ticket core metadata"
        />
        <details class="rounded-sm border border-subtle bg-bg-surface px-2.5 py-1.5">
          <summary class="focus-ring cursor-pointer rounded-xs text-xs font-medium text-fg-muted">
            Trace
          </summary>
          <UiMetadataStrip
            class="mt-2"
            :items="ticketTraceFacts"
            aria-label="Ticket trace metadata"
          />
        </details>
      </section>

      <section
        v-if="ticket.goal"
        class="space-y-2"
      >
        <h3 class="t-h3 text-fg-strong">
          Summary
        </h3>
        <p class="max-w-[76ch] text-sm leading-6 text-fg-default">
          {{ ticket.goal }}
        </p>
      </section>

      <UiCallout
        v-if="
          !isTerminalTrackerStatus(ticket.status) &&
            (ticket.blocker_reason || ticket.blocked_by.length)
        "
        tone="warning"
        density="compact"
      >
        {{ ticket.blocker_reason || `Blocked by ${ticket.blocked_by.join(', ')}` }}
      </UiCallout>

      <section
        v-if="ticket.dependency_keys.length || ticket.blocked_by.length"
        class="grid gap-4 border-t border-subtle pt-4 md:grid-cols-2"
      >
        <div
          v-if="ticket.dependency_keys.length"
          class="space-y-2"
        >
          <h3 class="t-h3 text-fg-strong">
            Depends on
          </h3>
          <div class="flex flex-wrap gap-1.5">
            <UiBadge
              v-for="item in ticket.dependency_keys"
              :key="item"
              variant="outline"
            >
              {{ item }}
            </UiBadge>
          </div>
        </div>
        <div
          v-if="ticket.blocked_by.length"
          class="space-y-2"
        >
          <h3 class="t-h3 text-fg-strong">
            Blocked by
          </h3>
          <div class="flex flex-wrap gap-1.5">
            <UiBadge
              v-for="item in ticket.blocked_by"
              :key="item"
              tone="warning"
              variant="outline"
            >
              {{ item }}
            </UiBadge>
          </div>
        </div>
      </section>

      <section
        v-if="ticket.definition_of_done_json.length || ticket.expected_changes_json.length"
        class="grid gap-4 border-t border-subtle pt-4 md:grid-cols-2"
      >
        <div
          v-if="ticket.definition_of_done_json.length"
          class="space-y-2"
        >
          <h3 class="t-h3 text-fg-strong">
            Definition of done
          </h3>
          <ul class="grid gap-1.5 text-sm leading-5 text-fg-default">
            <li
              v-for="item in ticket.definition_of_done_json"
              :key="item"
              class="rounded-sm bg-bg-surface-alt px-2 py-1"
            >
              {{ item }}
            </li>
          </ul>
        </div>
        <div
          v-if="ticket.expected_changes_json.length"
          class="space-y-2"
        >
          <h3 class="t-h3 text-fg-strong">
            Expected changes
          </h3>
          <ul class="grid gap-1.5 text-sm leading-5 text-fg-default">
            <li
              v-for="item in ticket.expected_changes_json"
              :key="item"
              class="rounded-sm bg-bg-surface-alt px-2 py-1"
            >
              {{ item }}
            </li>
          </ul>
        </div>
      </section>

      <section
        v-if="ticket.allowed_paths_json.length || ticket.constraints_json.length"
        class="grid gap-4 border-t border-subtle pt-4 md:grid-cols-2"
      >
        <div
          v-if="ticket.allowed_paths_json.length"
          class="space-y-2"
        >
          <h3 class="t-h3 text-fg-strong">
            Allowed paths
          </h3>
          <ul class="grid gap-1.5 text-xs leading-5 text-fg-default">
            <li
              v-for="item in ticket.allowed_paths_json"
              :key="item"
              class="break-all rounded-sm bg-bg-surface-alt px-2 py-1 font-mono"
            >
              {{ item }}
            </li>
          </ul>
        </div>
        <div
          v-if="ticket.constraints_json.length"
          class="space-y-2"
        >
          <h3 class="t-h3 text-fg-strong">
            Constraints
          </h3>
          <ul class="grid gap-1.5 text-sm leading-5 text-fg-default">
            <li
              v-for="item in ticket.constraints_json"
              :key="item"
              class="rounded-sm bg-bg-surface-alt px-2 py-1"
            >
              {{ item }}
            </li>
          </ul>
        </div>
      </section>

      <section
        v-if="ticket.outcome"
        class="space-y-2 border-t border-subtle pt-4"
      >
        <h3 class="t-h3 text-fg-strong">
          Outcome
        </h3>
        <p class="rounded-lg border border-subtle bg-bg-surface-alt px-3 py-2 text-sm leading-6 text-fg-default">
          {{ ticket.outcome }}
        </p>
      </section>

      <details
        v-if="hasTicketJson"
        class="rounded-lg border border-subtle bg-bg-surface px-3 py-2"
      >
        <summary class="focus-ring cursor-pointer rounded-xs text-xs font-medium text-fg-muted">
          Raw metadata
        </summary>
        <div class="mt-3 space-y-4">
          <div
            v-if="hasJsonObject(ticket.completion_evidence_json)"
            class="space-y-2"
          >
            <p class="text-xs font-medium text-fg-muted">
              Completion evidence
            </p>
            <UiJsonBlock
              :data="sanitizeForDisplay(ticket.completion_evidence_json)"
              density="compact"
              max-height="16rem"
              wrap
            />
          </div>
          <div
            v-if="hasJsonObject(ticket.source_json)"
            class="space-y-2"
          >
            <p class="text-xs font-medium text-fg-muted">
              Source
            </p>
            <UiJsonBlock
              :data="sanitizeForDisplay(ticket.source_json)"
              density="compact"
              max-height="16rem"
              wrap
            />
          </div>
          <div
            v-if="hasJsonObject(ticket.context_json)"
            class="space-y-2"
          >
            <p class="text-xs font-medium text-fg-muted">
              Context
            </p>
            <UiJsonBlock
              :data="sanitizeForDisplay(ticket.context_json)"
              density="compact"
              max-height="16rem"
              wrap
            />
          </div>
          <div
            v-if="hasJsonObject(ticket.metadata_json)"
            class="space-y-2"
          >
            <p class="text-xs font-medium text-fg-muted">
              Metadata
            </p>
            <UiJsonBlock
              :data="sanitizeForDisplay(ticket.metadata_json)"
              density="compact"
              max-height="16rem"
              wrap
            />
          </div>
        </div>
      </details>
    </div>
  </InspectableDetailDrawer>
</template>
