<script setup lang="ts">
// HomeView — root local status plus the project list. Page-specific logic lives
// in the home/* composables/components so this file stays as orchestration glue.

import { onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { UiButton, UiConfirmDialog, UiPageHeader, UiPageShell } from '@/components/ui'
import { usePolling } from '@/composables/usePolling'
import { isDesktopShell } from '@/lib/desktop'
import { GETTING_STARTED_URL } from '@/lib/externalLinks'
import { useProjectsStore } from '@/stores/projects'

import HomeProjectsSection from './home/HomeProjectsSection.vue'
import HomePortfolioOverview from './home/HomePortfolioOverview.vue'
import HomeSystemStatusCard from './home/HomeSystemStatusCard.vue'
import { useHomeAgentHostStatuses } from './home/useHomeAgentHostStatuses'
import { useHomePortfolioInsights } from './home/useHomePortfolioInsights'
import { useHomeSystemStatus } from './home/useHomeSystemStatus'

const projects = useProjectsStore()
const {
  items: projectItems,
  loading: projectsLoading,
  error: projectsError,
  activeProjectId,
} = storeToRefs(projects)

const isShell = isDesktopShell()
const repairOpen = ref(false)

const { hostStatuses, hostStatusSummary, loadHostStatuses, applyHostStatuses } =
  useHomeAgentHostStatuses(isShell)

const { health, systemBusy, statusTone, statusLabel, systemFacts, loadHealth, runSystemAction } =
  useHomeSystemStatus({
    onDoctorResult: applyHostStatuses,
    onRepairComplete: loadHostStatuses,
  })

const {
  insights,
  insightByProjectId,
  activeWork,
  totals: portfolioTotals,
  loading: portfolioLoading,
  failedProjectCount,
  load: loadPortfolioInsights,
} = useHomePortfolioInsights()

async function loadPortfolio(): Promise<void> {
  await projects.refresh()
  await loadPortfolioInsights(projectItems.value)
}

usePolling(loadPortfolio, { intervalMs: 20_000 })

onMounted(() => {
  void loadHealth()
  void loadHostStatuses()
})

function confirmRepair(): void {
  repairOpen.value = false
  void runSystemAction('repair')
}
</script>

<template>
  <UiPageShell>
    <UiPageHeader
      title="StackOS"
      description="Local runtime, projects, and agent-client readiness."
    >
      <template #actions>
        <UiButton
          :href="GETTING_STARTED_URL"
          target="_blank"
          rel="noopener noreferrer"
          variant="secondary"
          size="sm"
          icon-right="external-link"
          aria-label="Open the Getting Started guide in your browser"
        >
          Getting started
        </UiButton>
      </template>
    </UiPageHeader>

    <HomeSystemStatusCard
      :health="health"
      :is-shell="isShell"
      :status-tone="statusTone"
      :status-label="statusLabel"
      :system-facts="systemFacts"
      :system-busy="systemBusy"
      :host-statuses="hostStatuses"
      :host-status-summary="hostStatusSummary"
      @restart="runSystemAction('restart')"
      @doctor="runSystemAction('doctor')"
      @repair="repairOpen = true"
      @refresh-hosts="loadHostStatuses"
    />

    <HomePortfolioOverview
      :insights="insights"
      :active-work="activeWork"
      :totals="portfolioTotals"
      :loading="portfolioLoading"
      :failed-project-count="failedProjectCount"
    />

    <HomeProjectsSection
      :items="projectItems"
      :loading="projectsLoading"
      :error="projectsError"
      :current-project-id="activeProjectId"
      :insights="insightByProjectId"
    />

    <UiConfirmDialog
      v-model="repairOpen"
      title="Install or repair StackOS?"
      description="This reinstalls local StackOS assets and restarts the service. It's safe to run, but the service will be briefly unavailable."
      confirm-label="Install or repair"
      cancel-label="Cancel"
      tone="primary"
      @confirm="confirmRepair"
      @cancel="repairOpen = false"
    />
  </UiPageShell>
</template>
