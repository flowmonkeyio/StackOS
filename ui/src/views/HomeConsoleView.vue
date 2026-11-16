<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ProjectPageHeader } from '@/components/domain'
import { UiButton, UiCard, UiPageShell, UiSkeleton } from '@/components/ui'
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
const setup = useProjectSetup(projectId, { retainReminder: true })
const { state: setupState, error: setupError, writing: setupWriting, actionLabel, reminder, reminderUpdateCount } = setup
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
const { project, accounts, loading, connectionsLoaded, errors } = overview
const projects = useProjectsStore()
const currentProject = computed(() => projects.getById(projectId.value) ?? project.value)
const projectName = computed(() => projects.isProjectDeleted(projectId.value) ? 'Project unavailable'
  : currentProject.value?.name)
const description = computed(() => currentProject.value?.domain || 'Current project work')
const agents = useAgentStats(projectId)
onMounted(() => { void agents.load() })
const { summary: agentSummary, loading: agentsLoading, error: agentsError, filters: agentFilters } = agents
usePolling(overview.load, { intervalMs: 30_000 })
const refreshing = ref(false)
async function refreshAll(): Promise<void> {
  refreshing.value = true
  try {
    await Promise.all([overview.load(true), setup.refresh(), agents.load(), activeTasks.value?.refresh(), capacityControl.value?.refresh()])
  } finally { refreshing.value = false }
}
</script>

<template>
  <UiPageShell class="home-console">
    <ProjectPageHeader
      class="home-project-header"
      :project-id="projectId"
      :title="projectName"
      :description="description"
    >
      <template #actions>
        <div class="home-header-actions">
          <div class="home-setup-action">
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
          </div>
          <div class="home-capacity-action">
            <DeliveryCapacityControl
              :key="projectId"
              ref="capacityControl"
              :project-id="projectId"
            />
          </div>
          <UiButton
            class="home-refresh-action"
            variant="secondary"
            size="sm"
            icon-left="refresh"
            :loading="refreshing"
            @click="refreshAll"
          >
            Refresh
          </UiButton>
        </div>
      </template>
    </ProjectPageHeader>
    <div class="home-setup-slot">
      <UiCard
        v-if="!setupState && !reminder && !setupError"
        aria-label="Loading project setup"
        aria-busy="true"
        class="home-setup-placeholder"
      >
        <UiSkeleton
          :height="16"
          width="12rem"
        />
        <UiSkeleton
          :height="12"
          width="16rem"
          class="mt-2 max-w-full"
        />
      </UiCard>
      <UiCard
        v-if="reminder"
        aria-label="Project setup reminder"
      >
        <div class="home-setup-reminder-body flex flex-wrap items-center justify-between gap-3">
          <div class="min-w-0">
            <h2 class="t-h3">
              {{ reminder === 'setup' ? 'Set up this project' : 'Workflow updates available' }}
            </h2>
            <p class="mt-1 text-fg-muted">
              {{ reminder === 'setup' ? 'Let your agent prepare the workflows you need.' : `${reminderUpdateCount} workflows have new setup guidance.` }}
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
      :loaded="connectionsLoaded"
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

<style scoped>
.home-console {
  container: home-console / inline-size;
}
.home-header-actions {
  display: grid;
  grid-template-columns: repeat(3, max-content);
  align-items: center;
  justify-content: end;
  gap: 0.5rem;
}
.home-setup-action {
  grid-area: 1 / 1;
  min-block-size: 1.75rem;
}
.home-capacity-action {
  grid-area: 1 / 2;
}
.home-refresh-action {
  grid-area: 1 / 3;
}
/* Keep the existing reminder region above supervision stable even when its
   first read is slow or there is no applicable reminder. */
.home-setup-slot,
.home-setup-placeholder {
  box-sizing: border-box;
  min-height: 5rem;
}
@container home-console (min-width: 40rem) {
  /* Keep the title/description track unchanged when setup becomes eligible. */
  .home-header-actions {
    min-inline-size: 27rem;
  }
}
/* Query Home's available width, including the App sidebar/content padding. */
@container home-console (max-width: 40rem) {
  .home-project-header :deep(.ui-page-header__layout) {
    flex-direction: column;
  }
  .home-project-header :deep(.ui-page-header__actions),
  .home-header-actions {
    width: 100%;
  }
  .home-setup-reminder-body {
    flex-direction: column;
    align-items: flex-start;
  }
  .home-setup-slot,
  .home-setup-placeholder {
    min-height: 7.5rem;
  }
}
@container home-console (max-width: 26rem) {
  .home-header-actions {
    grid-template-columns: repeat(2, max-content);
  }
  .home-refresh-action {
    grid-area: 2 / 2;
  }
  .home-setup-slot,
  .home-setup-placeholder {
    min-height: 10rem;
  }
}
@container home-console (max-width: 20rem) {
  .home-setup-slot,
  .home-setup-placeholder {
    min-height: 11rem;
  }
  .home-setup-reminder-body > div:last-child {
    flex-direction: column;
    align-items: flex-start;
  }
  .home-header-actions {
    grid-template-columns: max-content;
  }
  .home-capacity-action {
    grid-area: 2 / 1;
  }
  .home-refresh-action {
    grid-area: 3 / 1;
  }
}
</style>
