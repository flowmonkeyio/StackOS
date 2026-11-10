<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { UiButton, UiCallout, UiConfirmDialog, UiDialog, UiFormField, UiInput, UiPageHeader, UiPageShell } from '@/components/ui'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useProjectsStore } from '@/stores/projects'
import { formatApiError } from '@/lib/client'
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
import AgentActivitySection from './home/AgentActivitySection.vue'
import { useAgentStats } from './home/useAgentStats'

const isShell = isDesktopShell()
const auth = useAuthStore()
const projects = useProjectsStore()
const router = useRouter()
const createOpen = ref(false)
const projectName = ref('')
const projectSlug = ref('')
const projectDomain = ref('')
const createError = ref('')
const repairOpen = ref(false)
const portfolio = useHomePortfolioInsights()
const { items, loading: projectsLoading, error: projectsError, nextCursor, total, filters } = portfolio
const tickets = useTicketCounts()
const agents = useAgentStats()
const { summary: agentSummary, loading: agentsLoading, error: agentsError, filters: agentFilters } = agents
const { summary, visibility, loading: countsLoading, error: countsError } = tickets
const { hostStatuses, hostStatusSummary, loadHostStatuses, applyHostStatuses } =
  useHomeAgentHostStatuses(isShell)
const { health, systemBusy, statusTone, statusLabel, systemFacts, loadHealth, runSystemAction } =
  useHomeSystemStatus({ onDoctorResult: applyHostStatuses, onRepairComplete: loadHostStatuses })

usePolling(async () => { if (auth.mode !== 'server') await tickets.load() }, { intervalMs: 30_000 })
onMounted(() => {
  if (auth.mode !== 'server') {
    void agents.load()
    void loadHealth()
    void loadHostStatuses()
  }
  void portfolio.load()
})

async function refresh(): Promise<void> {
  if (auth.mode === 'server') { await portfolio.load(); return }
  await Promise.all([portfolio.load(), tickets.load(), agents.load(), loadHealth(), loadHostStatuses()])
}
async function createProject(): Promise<void> {
  createError.value = ''
  try {
    const project = await projects.createProject({ name: projectName.value.trim(), slug: projectSlug.value.trim(), domain: projectDomain.value.trim(), locale: 'en-US' })
    createOpen.value = false
    await router.push(`/projects/${project.id}`)
  } catch (err) {
    createError.value = formatApiError(err, 'Could not create project.')
  }
}
function confirmRepair(): void {
  repairOpen.value = false
  void runSystemAction('repair')
}
async function setVisibility(value: string | number): Promise<void> {
  const active = value === 'all' ? null : value === 'active'
  await Promise.all([
    tickets.setVisibility(active),
    agents.setVisibility(active),
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
      :description="auth.mode === 'server' ? 'Your private projects' : 'Your local runtime and projects'"
    >
      <template #actions>
        <UiButton
          v-if="auth.mode === 'server'"
          variant="primary"
          @click="createOpen = true"
        >
          Create project
        </UiButton>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="projectsLoading || countsLoading || agentsLoading"
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
      v-if="auth.mode !== 'server'"
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
      v-if="auth.mode !== 'server'"
      :summary="summary"
      :visibility="visibility"
      :selected-status="filters.ticket_status"
      :loading="countsLoading"
      :error="countsError"
      @visibility="setVisibility"
      @select="selectStatus"
    />
    <AgentActivitySection
      v-if="auth.mode !== 'server'"
      :summary="agentSummary"
      :loading="agentsLoading"
      :error="agentsError"
      :filters="agentFilters"
      global
      @filters="agents.setFilters"
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
    <UiDialog
      v-model="createOpen"
      title="Create private project"
    >
      <form
        class="flex flex-col gap-4"
        @submit.prevent="createProject"
      >
        <UiCallout
          v-if="createError"
          tone="danger"
        >
          {{ createError }}
        </UiCallout>
        <UiFormField
          label="Project name"
          required
        >
          <UiInput
            v-model="projectName"
            required
            :disabled="projects.loading"
          />
        </UiFormField>
        <UiFormField
          label="Project slug"
          help="Use lowercase letters, numbers and hyphens."
          required
        >
          <UiInput
            v-model="projectSlug"
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
            required
            :disabled="projects.loading"
          />
        </UiFormField>
        <UiFormField
          label="Domain"
          required
        >
          <UiInput
            v-model="projectDomain"
            required
            :disabled="projects.loading"
          />
        </UiFormField>
        <UiButton
          type="submit"
          variant="primary"
          :loading="projects.loading"
        >
          Create project
        </UiButton>
      </form>
    </UiDialog>
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
