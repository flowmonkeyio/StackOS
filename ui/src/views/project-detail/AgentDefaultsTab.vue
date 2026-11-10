<script setup lang="ts">
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import ProjectAgents from '../agents/ProjectAgents.vue'

const { projectId } = useProjectRouteScope()
</script>

<template>
  <ProjectAgents
    :key="projectId"
    :project-id="projectId"
  />
</template>
