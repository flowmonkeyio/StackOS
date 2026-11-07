<script setup lang="ts">
import { UiButton } from '@/components/ui'
import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import type { TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'
import TrackerTicketDetailContent from './TrackerTicketDetailContent.vue'

defineProps<{
  modelValue: boolean
  ticket: TrackerTicket | null
  parentTask?: TrackerTask | null
  title: string
  description?: string
}>()

defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()
</script>

<template>
  <InspectableDetailDrawer
    :model-value="modelValue"
    :title="title"
    :description="description"
    size="lg"
    :has-detail="Boolean(ticket)"
    empty-title="No ticket selected"
    empty-description="Pick a ticket from the graph."
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <TrackerTicketDetailContent
      :ticket="ticket"
      :parent-task="parentTask"
    />
    <template #footer>
      <UiButton
        variant="secondary"
        size="sm"
        @click="$emit('update:modelValue', false)"
      >
        Close
      </UiButton>
    </template>
  </InspectableDetailDrawer>
</template>
