<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ProjectPageHeader } from '@/components/domain'
import { UiButton, UiCard, UiPageShell } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { usePolling } from '@/composables/usePolling'
import { useProjectsStore } from '@/stores/projects'
import ActiveTaskList from './home/ActiveTaskList.vue'
import DeliveryCapacityControl from './home/DeliveryCapacityControl.vue'
import HomeConnectionsSection from './home/HomeConnectionsSection.vue'
import AgentActivitySection from './home/AgentActivitySection.vue'
import { useAgentStats } from './home/useAgentStats'
import { useProjectOverview } from './home/useProjectOverview'
import { useProjectSetup } from './project-setup/useProjectSetup'
import ProjectSetupLaunchDialog from './project-setup/ProjectSetupLaunchDialog.vue'

const { projectId } = useProjectRouteScope()
const router = useRouter()
const setup = useProjectSetup(projectId)
const { state: setupState, error: setupError, writing: setupWriting, actionLabel, reminder } = setup
const launchOpen = ref(false)
function openSetup(): void {
  if (setup.opensLaunch.value && !setupError.value) launchOpen.value = true
  else void router.push(`/projects/${projectId.value}/setup`)
}
function viewSetup(): void {
  launchOpen.value = false
  void router.push(`/projects/${projectId.value}/setup`)
}
const activeTasks = ref<InstanceType<typeof ActiveTaskList> | null>(null)
const capacityControl = ref<InstanceType<typeof DeliveryCapacityControl> | null>(null)
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
  await Promise.all([refresh(), setup.refresh(), agents.load(), activeTasks.value?.refresh(), capacityControl.value?.refresh()])
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
          v-if="setupState?.eligible"
          size="sm"
          :disabled="setupWriting"
          @click="openSetup"
        >
          {{ actionLabel }}<span
            v-if="setupState.update_count"
            class="text-fg-muted"
            :aria-label="`${setupState.update_count} workflow updates available`"
          > · {{ setupState.update_count }}</span>
        </UiButton>
        <DeliveryCapacityControl
          :key="projectId"
          ref="capacityControl"
        />
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
    <UiCard
      v-if="reminder"
      aria-label="Project setup reminder"
    >
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="min-w-0">
          <h2 class="t-h3">
            {{ reminder === 'setup' ? 'Set up this project' : 'Workflow updates available' }}
          </h2>
          <p class="mt-1 text-fg-muted">
            {{ reminder === 'setup' ? 'Let your agent prepare the workflows you need.' : `${setupState?.update_count} workflows have new setup guidance.` }}
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <UiButton
            variant="ghost"
            size="sm"
            :loading="setupWriting"
            @click="setup.dismiss(reminder)"
          >
            Don't show again
          </UiButton>
          <UiButton
            variant="primary"
            size="sm"
            :disabled="setupWriting"
            @click="openSetup"
          >
            {{ reminder === 'setup' ? 'Set up project' : 'Review updates' }}
          </UiButton>
        </div>
      </div>
    </UiCard>
    <div
      v-if="setupError || setupState?.folder_status === 'unavailable'"
      role="status"
      class="flex flex-wrap items-center gap-2 text-xs text-fg-muted"
    >
      <span>{{ setupError || 'The project folder is unavailable. Setup history is retained. Restore access to the folder, then check again.' }}</span>
      <UiButton
        size="sm"
        :loading="setup.running.value"
        @click="setup.refresh"
      >
        Check again
      </UiButton>
    </div>
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
    <ProjectSetupLaunchDialog
      v-if="launchOpen && setupState"
      :state="setupState"
      :pending="setupWriting"
      :error="setupError"
      :start="setup.start"
      @close="launchOpen = false"
      @started="viewSetup"
    />
  </UiPageShell>
</template>
