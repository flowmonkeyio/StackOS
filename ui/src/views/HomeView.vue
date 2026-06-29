<script setup lang="ts">
// HomeView — root local status plus the project list. Page-specific logic lives
// in the home/* composables/components so this file stays as orchestration glue.

import { onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { UiConfirmDialog, UiPageHeader, UiPageShell } from '@/components/ui'
import { isDesktopShell } from '@/lib/desktop'
import { useProjectsStore } from '@/stores/projects'

import HomeProjectsSection from './home/HomeProjectsSection.vue'
import HomeSystemStatusCard from './home/HomeSystemStatusCard.vue'
import { useHomeAgentHostStatuses } from './home/useHomeAgentHostStatuses'
import { useHomeSystemStatus } from './home/useHomeSystemStatus'

const projects = useProjectsStore()
const { items: projectItems, loading: projectsLoading, error: projectsError } = storeToRefs(projects)

const isShell = isDesktopShell()
const repairOpen = ref(false)

const { hostStatuses, hostStatusSummary, loadHostStatuses, applyHostStatuses } =
  useHomeAgentHostStatuses(isShell)

const { health, systemBusy, statusTone, statusLabel, systemFacts, loadHealth, runSystemAction } =
  useHomeSystemStatus({
    onDoctorResult: applyHostStatuses,
    onRepairComplete: loadHostStatuses,
  })

onMounted(() => {
  void projects.refresh()
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
      description="Your local, agent-run operations."
    />

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

    <HomeProjectsSection
      :items="projectItems"
      :loading="projectsLoading"
      :error="projectsError"
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
