<script setup lang="ts">
import { computed } from 'vue'

import { UiBadge } from '@/components/ui'
import { resolveStatus } from '@/design/status'
import type { TrackerStatus } from '@/lib/task-tracker/types'

const props = defineProps<{
  status: TrackerStatus | string
}>()

const definition = computed(() => resolveStatus('tracker', props.status))
</script>

<template>
  <UiBadge
    :tone="definition.tone"
    variant="subtle"
    size="sm"
    :dot="definition.dot"
    :pulse="definition.inFlight"
  >
    {{ definition.label }}
  </UiBadge>
</template>
