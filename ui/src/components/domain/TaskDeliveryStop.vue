<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/components/StatusBadge.vue'
import type { TaskDeliveryState } from '@/lib/task-tracker/delivery'
import type { TrackerTicket } from '@/lib/task-tracker/types'

const props = defineProps<{
  delivery: TaskDeliveryState | null
  tickets?: Pick<TrackerTicket, 'key' | 'title'>[]
  detailed?: boolean
}>()
const ticketLabel = computed(() => props.tickets?.find(ticket => ticket.key === props.delivery?.ticketKey)?.title || props.delivery?.ticketKey)
</script>

<template>
  <span
    v-if="delivery?.status === 'stopped'"
    class="task-delivery-stop"
    :title="delivery.reason ?? undefined"
  >
    <span class="task-delivery-stop__summary">
      <StatusBadge
        domain="delivery"
        status="stopped"
      />
      <span
        v-if="ticketLabel"
        class="task-delivery-stop__ticket"
        :title="ticketLabel"
      >{{ ticketLabel }}</span>
    </span>
    <span
      v-if="detailed && delivery.reason"
      class="task-delivery-stop__reason"
    >{{ delivery.reason }}</span>
  </span>
</template>

<style scoped>
.task-delivery-stop { display: block; min-width: 0; max-width: 100%; font-size: 12px; }
.task-delivery-stop__summary { display: flex; align-items: center; gap: 8px; min-width: 0; }
.task-delivery-stop__ticket { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-fg-muted); }
.task-delivery-stop__reason { display: block; margin-top: 6px; line-height: 18px; overflow-wrap: anywhere; color: var(--color-fg-default); }
</style>
