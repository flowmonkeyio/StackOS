<script setup lang="ts">
import { UiPageShell } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import ProjectAgents from './agents/ProjectAgents.vue'

const { projectId, isValid } = useProjectRouteScope()
</script>

<template>
  <UiPageShell>
    <ProjectAgents
      v-if="isValid"
      :key="projectId"
      :project-id="projectId"
      standalone
    />
  </UiPageShell>
</template>
