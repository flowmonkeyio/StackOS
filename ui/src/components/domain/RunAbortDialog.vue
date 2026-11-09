<script setup lang="ts">
import { computed, onBeforeUnmount } from 'vue'
import { UiCallout, UiConfirmDialog } from '@/components/ui'
import { useRunAbort } from '@/composables/useRunAbort'

const { selected, open, pending, error, outcome, abort, close } = useRunAbort()
const description = computed(() => `${selected.value?.title ?? ''}. ${selected.value?.automated
  ? 'Stops only StackOS-owned automated invocations. External agent sessions stay open.'
  : 'Changes run status only. Your external agent session stays open.'} Completed work is kept. Future schedules stay enabled.`)
onBeforeUnmount(close)
</script>

<template>
  <UiConfirmDialog
    :model-value="open"
    :title="outcome ? 'Run aborted' : 'Abort run?'"
    :description="description"
    :confirm-label="outcome ? 'Done' : pending ? 'Aborting…' : error ? 'Retry abort' : 'Abort run'"
    :loading="pending"
    tone="danger"
    @update:model-value="!$event && close()"
    @confirm="outcome ? close() : abort()"
  >
    <UiCallout
      v-if="error"
      tone="danger"
      title="Stop not confirmed"
    >
      {{ error }}
    </UiCallout>
    <p
      v-if="outcome"
      role="status"
      class="text-sm text-fg-default"
    >
      {{ outcome }}
    </p>
  </UiConfirmDialog>
</template>
