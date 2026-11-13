<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ProjectPageHeader } from '@/components/domain'
import { UiButton, UiPageShell } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { usePolling } from '@/composables/usePolling'
import { useProjectsStore } from '@/stores/projects'
import ActiveTaskList from './home/ActiveTaskList.vue'
import DeliveryCapacityControl from './home/DeliveryCapacityControl.vue'
import HomeConnectionsSection from './home/HomeConnectionsSection.vue'
import AgentActivitySection from './home/AgentActivitySection.vue'
import { useAgentStats } from './home/useAgentStats'
import { useProjectOverview } from './home/useProjectOverview'

const { projectId } = useProjectRouteScope()
const activeTasks = ref<InstanceType<typeof ActiveTaskList> | null>(null)
const capacityControl = ref<InstanceType<typeof DeliveryCapacityControl> | null>(null)
const overview = useProjectOverview(projectId)
const { project, accounts, loading, errors } = overview
const projects = useProjectsStore()
const currentProject = computed(() => projects.getById(projectId.value) ?? project.value)
const projectName = computed(() => projects.isProjectDeleted(projectId.value) ? 'Project unavailable'
  : currentProject.value?.name)
const description = computed(() => currentProject.value?.domain || 'Current project work')
const agents = useAgentStats(projectId)
onMounted(() => { void agents.load() })
const { summary: agentSummary, loading: agentsLoading, error: agentsError, filters: agentFilters } = agents
const { running } = usePolling(overview.load, { intervalMs: 30_000 })
const refreshing = ref(false)
async function refreshAll(): Promise<void> {
  refreshing.value = true
  try {
    await Promise.all([overview.load(true), agents.load(), activeTasks.value?.refresh(), capacityControl.value?.refresh()])
  } finally { refreshing.value = false }
}
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      :title="projectName"
      :description="description"
    >
      <template #actions>
        <DeliveryCapacityControl
          :key="projectId"
          ref="capacityControl"
        />
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="running || refreshing"
          @click="refreshAll"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>
    <ActiveTaskList
      :key="projectId"
      ref="activeTasks"
      :project-id="projectId"
    />
    <AgentActivitySection
      :summary="agentSummary"
      :loading="agentsLoading"
      :error="agentsError"
      :filters="agentFilters"
      @filters="agents.setFilters"
    />
    <HomeConnectionsSection
      :accounts="accounts"
      :loading="loading"
      :error="errors.connections"
      :project-id="projectId"
    />
  </UiPageShell>
</template>
