<script setup lang="ts">
import { UiBadge } from '@/components/ui'
import { taskPriority } from './taskPriority'
defineProps<{ priority: string }>()
</script>

<template>
  <UiBadge
    :tone="taskPriority(priority).tone"
    :aria-label="`Priority ${taskPriority(priority).label}`"
  >
    {{ taskPriority(priority).label }}
  </UiBadge>
</template>
