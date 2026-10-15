<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { UiButton, UiConfirmDialog, UiPageHeader, UiPageShell } from '@/components/ui'
import { isDesktopShell } from '@/lib/desktop'
import { GETTING_STARTED_URL } from '@/lib/externalLinks'
import { usePolling } from '@/composables/usePolling'
import type { TrackerStatus } from '@/lib/task-tracker/types'
import HomeProjectsSection from './home/HomeProjectsSection.vue'
import HomePortfolioOverview from './home/HomePortfolioOverview.vue'
import HomeSystemStatusCard from './home/HomeSystemStatusCard.vue'
import { useHomeAgentHostStatuses } from './home/useHomeAgentHostStatuses'
import { useHomePortfolioInsights } from './home/useHomePortfolioInsights'
import { useHomeSystemStatus } from './home/useHomeSystemStatus'
import { useTicketCounts } from './home/useTicketCounts'

const isShell = isDesktopShell()
const repairOpen = ref(false)
const portfolio = useHomePortfolioInsights()
const { items, loading: projectsLoading, error: projectsError, nextCursor, total, filters } = portfolio
const tickets = useTicketCounts()
const { summary, visibility, loading: countsLoading, error: countsError } = tickets
const { hostStatuses, hostStatusSummary, loadHostStatuses, applyHostStatuses } =
  useHomeAgentHostStatuses(isShell)
const { health, systemBusy, statusTone, statusLabel, systemFacts, loadHealth, runSystemAction } =
  useHomeSystemStatus({ onDoctorResult: applyHostStatuses, onRepairComplete: loadHostStatuses })

usePolling(tickets.load, { intervalMs: 30_000 })
onMounted(() => {
  void loadHealth()
  void loadHostStatuses()
  void portfolio.load()
})

async function refresh(): Promise<void> {
  await Promise.all([portfolio.load(), tickets.load(), loadHealth(), loadHostStatuses()])
}
function confirmRepair(): void {
  repairOpen.value = false
  void runSystemAction('repair')
}
async function setVisibility(value: string | number): Promise<void> {
  const active = value === 'all' ? null : value === 'active'
  await Promise.all([
    tickets.setVisibility(active),
    portfolio.setFilters({ ...filters.value, is_active: active }),
  ])
}
function selectStatus(status: TrackerStatus | null): void {
  void portfolio.setFilters({ ...filters.value, ticket_status: status })
}
</script>

<template>
  <UiPageShell>
    <UiPageHeader
      title="Overview"
      description="Your local runtime and projects"
    >
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="projectsLoading || countsLoading"
          @click="refresh"
        >
          Refresh
        </UiButton>
        <UiButton
          :href="GETTING_STARTED_URL"
          target="_blank"
          rel="noopener noreferrer"
          variant="ghost"
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
      :summary="summary"
      :visibility="visibility"
      :selected-status="filters.ticket_status"
      :loading="countsLoading"
      :error="countsError"
      @visibility="setVisibility"
      @select="selectStatus"
    />
    <HomeProjectsSection
      :items="items"
      :loading="projectsLoading"
      :error="projectsError"
      :next-cursor="nextCursor"
      :total="total"
      :filters="filters"
      @filters="portfolio.setFilters"
      @more="portfolio.loadMore"
      @clear-status="selectStatus(null)"
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
