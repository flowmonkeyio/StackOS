<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ProjectPageHeader } from '@/components/domain'
import { UiButton, UiPageShell } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { usePolling } from '@/composables/usePolling'
import { useProjectsStore } from '@/stores/projects'
import ActiveTaskList from './home/ActiveTaskList.vue'
import HomeConnectionsSection from './home/HomeConnectionsSection.vue'
import AgentActivitySection from './home/AgentActivitySection.vue'
import { useAgentStats } from './home/useAgentStats'
import { useProjectOverview } from './home/useProjectOverview'

const { projectId } = useProjectRouteScope()
const activeTasks = ref<InstanceType<typeof ActiveTaskList> | null>(null)
const overview = useProjectOverview(projectId)
const { project, accounts, loading, errors } = overview
const projects = useProjectsStore()
const projectName = computed(() => projects.isProjectDeleted(projectId.value) ? 'Project unavailable'
  : projects.getById(projectId.value)?.name ?? project.value?.name)
const description = computed(() => projects.getById(projectId.value)?.domain || project.value?.domain || 'Current project work')
const agents = useAgentStats(projectId)
onMounted(() => { void agents.load() })
const { summary: agentSummary, loading: agentsLoading, error: agentsError, filters: agentFilters } = agents
const { running, refresh } = usePolling(overview.load, { intervalMs: 30_000 })
async function refreshAll(): Promise<void> {
  await Promise.all([refresh(), agents.load(), activeTasks.value?.refresh()])
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
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="running"
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
