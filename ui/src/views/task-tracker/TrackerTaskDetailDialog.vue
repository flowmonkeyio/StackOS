<script setup lang="ts">
import { UiDialog } from '@/components/ui'
import { formatDateTime } from '@/lib/stackos/json'
import type { TrackerTask } from '@/lib/task-tracker/types'

import { formatJsonBlock, hasJsonObject } from './detailUtils'
import TrackerStatusBadge from './TrackerStatusBadge.vue'

defineProps<{
  modelValue: boolean
  task: TrackerTask | null
}>()

defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()
</script>

<template>
  <UiDialog
    :model-value="modelValue"
    :title="task?.title ?? 'Task detail'"
    :description="task?.key"
    size="lg"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div v-if="task" class="tracker-detail__body">
      <div class="tracker-detail__drawer-kicker">
        <p class="tracker-detail__eyebrow">Task</p>
        <TrackerStatusBadge :status="task.status" />
      </div>
      <p v-if="task.goal || task.description" class="tracker-detail__description">
        {{ task.goal || task.description }}
      </p>
      <div class="tracker-detail__facts">
        <div class="tracker-detail-fact">
          <span>Owner</span>
          <strong>{{ task.owner ?? '-' }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Priority</span>
          <strong>{{ task.priority_key }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Lane</span>
          <strong>{{ task.lane_key }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Type</span>
          <strong>{{ task.task_type }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Source</span>
          <strong>{{ task.source_kind }}</strong>
        </div>
        <div class="tracker-detail-fact">
          <span>Updated</span>
          <strong>{{ formatDateTime(task.updated_at) }}</strong>
        </div>
      </div>
      <div v-if="task.definition_of_done_json.length" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Definition of done</p>
        <ul class="tracker-detail-list">
          <li v-for="item in task.definition_of_done_json" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div v-if="task.expected_outcomes_json.length" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Expected outcomes</p>
        <ul class="tracker-detail-list">
          <li v-for="item in task.expected_outcomes_json" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div v-if="task.constraints_json.length" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Constraints</p>
        <ul class="tracker-detail-list">
          <li v-for="item in task.constraints_json" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div v-if="hasJsonObject(task.completion_evidence_json)" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Completion evidence</p>
        <pre class="tracker-detail-json">{{ formatJsonBlock(task.completion_evidence_json) }}</pre>
      </div>
      <div v-if="hasJsonObject(task.source_json)" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Source</p>
        <pre class="tracker-detail-json">{{ formatJsonBlock(task.source_json) }}</pre>
      </div>
      <div v-if="hasJsonObject(task.context_json)" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Context</p>
        <pre class="tracker-detail-json">{{ formatJsonBlock(task.context_json) }}</pre>
      </div>
      <div v-if="hasJsonObject(task.metadata_json)" class="tracker-detail-section">
        <p class="tracker-detail-section__title">Metadata</p>
        <pre class="tracker-detail-json">{{ formatJsonBlock(task.metadata_json) }}</pre>
      </div>
    </div>
  </UiDialog>
</template>
