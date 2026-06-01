<script setup lang="ts">
import { UiCallout, UiEmptyState, UiSidePanel } from '@/components/ui'
import { formatDateTime } from '@/lib/stackos/json'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'
import type { TrackerTicket } from '@/lib/task-tracker/types'

import { formatJsonBlock, hasJsonObject } from './detailUtils'
import TrackerStatusBadge from './TrackerStatusBadge.vue'

defineProps<{
  modelValue: boolean
  ticket: TrackerTicket | null
  title: string
  description?: string
}>()

defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    :title="title"
    :description="description"
    size="lg"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div v-if="ticket" class="tracker-detail__body tracker-detail__body--drawer">
      <div class="tracker-detail__drawer-kicker">
        <p class="tracker-detail__eyebrow">Ticket</p>
        <TrackerStatusBadge :status="ticket.status" />
      </div>
      <p v-if="ticket.goal" class="tracker-detail__description">{{ ticket.goal }}</p>
      <div class="tracker-detail__facts">
        <div class="tracker-detail-fact">
          <span>Task</span>
          <strong>{{ ticket.task_key }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Assignee</span>
          <strong>{{ ticket.assignee ?? '-' }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Priority</span>
          <strong>{{ ticket.priority_key }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Lane</span>
          <strong>{{ ticket.lane_key }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Run plan</span>
          <strong>{{ ticket.run_plan_id ?? '-' }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Parent</span>
          <strong>{{ ticket.parent_ticket_key ?? '-' }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Source</span>
          <strong>{{ ticket.source_kind }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Updated</span>
          <strong>{{ formatDateTime(ticket.updated_at) }}</strong>
        </div>
      </div>
      <UiCallout
        v-if="
          !isTerminalTrackerStatus(ticket.status) &&
          (ticket.blocker_reason || ticket.blocked_by.length)
        "
        tone="warning"
      >
        {{ ticket.blocker_reason || `Blocked by ${ticket.blocked_by.join(', ')}` }}
      </UiCallout>
      <div v-if="ticket.definition_of_done_json.length" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Definition of done</p>
        <ul class="tracker-detail-list">
          <li v-for="item in ticket.definition_of_done_json" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div v-if="ticket.expected_changes_json.length" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Expected changes</p>
        <ul class="tracker-detail-list">
          <li v-for="item in ticket.expected_changes_json" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div v-if="ticket.allowed_paths_json.length" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Allowed paths</p>
        <ul class="tracker-detail-list tracker-detail-list--mono">
          <li v-for="item in ticket.allowed_paths_json" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div v-if="ticket.constraints_json.length" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Constraints</p>
        <ul class="tracker-detail-list">
          <li v-for="item in ticket.constraints_json" :key="item">{{ item }}</li>
        </ul>
      </div>
      <p v-if="ticket.outcome" class="tracker-detail__outcome">{{ ticket.outcome }}</p>
      <div v-if="hasJsonObject(ticket.completion_evidence_json)" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Completion evidence</p>
        <pre class="tracker-detail-json">{{ formatJsonBlock(ticket.completion_evidence_json) }}</pre>
      </div>
      <div v-if="hasJsonObject(ticket.source_json)" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Source</p>
        <pre class="tracker-detail-json">{{ formatJsonBlock(ticket.source_json) }}</pre>
      </div>
      <div v-if="hasJsonObject(ticket.context_json)" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Context</p>
        <pre class="tracker-detail-json">{{ formatJsonBlock(ticket.context_json) }}</pre>
      </div>
      <div v-if="hasJsonObject(ticket.metadata_json)" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Metadata</p>
        <pre class="tracker-detail-json">{{ formatJsonBlock(ticket.metadata_json) }}</pre>
      </div>
    </div>

    <UiEmptyState v-else title="Select work" description="Pick a ticket from the graph or table." />
  </UiSidePanel>
</template>
