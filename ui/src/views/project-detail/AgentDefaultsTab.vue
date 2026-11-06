<script setup lang="ts">
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import AgentDefaultsForm from './agent-defaults/AgentDefaultsForm.vue'

const { projectId } = useProjectRouteScope()
</script>

<template>
  <AgentDefaultsForm
    :key="projectId"
    :project-id="projectId"
  />
</template>
