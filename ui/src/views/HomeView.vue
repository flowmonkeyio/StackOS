<script setup lang="ts">
// HomeView — the root portfolio + local system status.
//
// Replaces the daemon-health JSON dump with a calm home base: a humanized
// system status (with desktop-only service controls), and the project(s) this
// machine operates. Picking a project opens its operations console.

import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiConfirmDialog,
  UiEmptyState,
  UiIcon,
  UiMetadataStrip,
  UiPageHeader,
  UiPageShell,
  UiSkeleton,
} from '@/components/ui'
import type { UiMetadataStripItem } from '@/components/ui/UiMetadataStrip.vue'
import { apiFetch } from '@/lib/client'
import { desktop, isDesktopShell } from '@/lib/desktop'
import { formatDurationMinutes } from '@/lib/stackos/time'
import { useProjectsStore } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'
import type { SchemaHealthResponse } from '@/api'

const projects = useProjectsStore()
const toasts = useToastsStore()
const { items: projectItems, loading: projectsLoading, error: projectsError } = storeToRefs(projects)

const isShell = isDesktopShell()

type HealthState =
  | { kind: 'loading' }
  | { kind: 'ok'; data: SchemaHealthResponse }
  | { kind: 'down' }

const health = ref<HealthState>({ kind: 'loading' })
const systemBusy = ref<string | null>(null)
const repairOpen = ref(false)

async function loadHealth(): Promise<void> {
  try {
    const data = await apiFetch<SchemaHealthResponse>('/api/v1/health')
    health.value = { kind: 'ok', data }
  } catch {
    health.value = { kind: 'down' }
  }
}

onMounted(() => {
  void projects.refresh()
  void loadHealth()
})

const statusTone = computed<'success' | 'warning' | 'danger' | 'neutral'>(() => {
  if (health.value.kind === 'loading') return 'neutral'
  if (health.value.kind === 'down') return 'danger'
  return health.value.data.db_status === 'ok' ? 'success' : 'warning'
})

const statusLabel = computed(() => {
  if (health.value.kind === 'loading') return 'Checking…'
  if (health.value.kind === 'down') return 'Service unreachable'
  return health.value.data.db_status === 'ok' ? 'Running' : 'Storage degraded'
})

const systemFacts = computed<UiMetadataStripItem[]>(() => {
  if (health.value.kind !== 'ok') return []
  const d = health.value.data
  const facts: UiMetadataStripItem[] = []
  if (d.version) facts.push({ label: 'Version', value: `v${d.version}` })
  if (typeof d.daemon_uptime_s === 'number') {
    facts.push({ label: 'Uptime', value: formatDurationMinutes(d.daemon_uptime_s / 60) })
  }
  facts.push({ label: 'Storage', value: d.db_status === 'ok' ? 'Healthy' : 'Degraded' })
  facts.push({ label: 'Automation', value: d.scheduler_running ? 'On' : 'Off' })
  return facts
})

const STATUS_DOT: Record<string, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  neutral: 'bg-fg-subtle',
}

type SystemAction = 'restart' | 'doctor' | 'updates' | 'repair'

async function runSystemAction(action: SystemAction): Promise<void> {
  systemBusy.value = action
  try {
    if (action === 'restart') {
      const r = await desktop.restartService()
      toastResult(r?.ok ?? false, 'Service restarted', 'Restart failed')
      await loadHealth()
    } else if (action === 'doctor') {
      const r = await desktop.runDoctor()
      toastResult(r?.ok ?? false, 'Doctor passed', 'Doctor found issues — see the StackOS log')
    } else if (action === 'updates') {
      const r = await desktop.checkForUpdates()
      const status = r?.status ?? 'unknown'
      toasts.info('Checked for updates', `Update status: ${status}.`)
    } else if (action === 'repair') {
      const r = await desktop.installOrRepair()
      toastResult(r?.ok ?? false, 'Install or repair complete', 'Install or repair failed')
      await loadHealth()
    }
  } finally {
    systemBusy.value = null
  }
}

function toastResult(ok: boolean, okMsg: string, failMsg: string): void {
  if (ok) toasts.success(okMsg)
  else toasts.error(failMsg)
}

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

    <!-- System status -->
    <UiCard
      section
      aria-label="System status"
    >
      <template #header>
        <div class="flex items-center gap-2.5">
          <span
            :class="['inline-block h-2 w-2 shrink-0 rounded-full', STATUS_DOT[statusTone]]"
            aria-hidden="true"
          />
          <h2 class="t-h3 text-fg-strong">
            Local service
          </h2>
          <UiBadge :tone="statusTone === 'neutral' ? 'neutral' : statusTone">
            {{ statusLabel }}
          </UiBadge>
        </div>
        <UiButton
          v-if="isShell"
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="systemBusy === 'restart'"
          @click="runSystemAction('restart')"
        >
          Restart
        </UiButton>
      </template>

      <div
        v-if="health.kind === 'loading'"
        class="space-y-2"
      >
        <UiSkeleton
          shape="line"
          width="22rem"
        />
      </div>
      <UiCallout
        v-else-if="health.kind === 'down'"
        tone="danger"
        title="Can’t reach the local service"
      >
        StackOS isn’t responding on this machine.
        <template
          v-if="isShell"
          #actions
        >
          <UiButton
            variant="secondary"
            size="sm"
            :loading="systemBusy === 'restart'"
            @click="runSystemAction('restart')"
          >
            Restart service
          </UiButton>
          <UiButton
            variant="secondary"
            size="sm"
            :loading="systemBusy === 'repair'"
            @click="repairOpen = true"
          >
            Install or repair
          </UiButton>
        </template>
      </UiCallout>
      <div
        v-else
        class="space-y-3"
      >
        <UiMetadataStrip
          :items="systemFacts"
          aria-label="System facts"
        />
        <div
          v-if="isShell"
          class="flex flex-wrap gap-2"
        >
          <UiButton
            variant="secondary"
            size="sm"
            icon-left="shield-check"
            :loading="systemBusy === 'doctor'"
            @click="runSystemAction('doctor')"
          >
            Run doctor
          </UiButton>
          <UiButton
            variant="secondary"
            size="sm"
            icon-left="arrow-right"
            :loading="systemBusy === 'updates'"
            @click="runSystemAction('updates')"
          >
            Check for updates
          </UiButton>
          <UiButton
            variant="secondary"
            size="sm"
            icon-left="wrench"
            :loading="systemBusy === 'repair'"
            @click="repairOpen = true"
          >
            Install or repair
          </UiButton>
        </div>
        <p
          v-else
          class="text-2xs text-fg-subtle"
        >
          Service controls live in the StackOS desktop app.
        </p>
      </div>
    </UiCard>

    <!-- Projects -->
    <section
      aria-label="Projects"
      class="space-y-3"
    >
      <h2 class="t-h3 text-fg-strong">
        Projects
      </h2>

      <UiCallout
        v-if="projectsError"
        tone="danger"
      >
        {{ projectsError }}
      </UiCallout>

      <div
        v-if="projectsLoading && projectItems.length === 0"
        class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
      >
        <UiSkeleton
          v-for="n in 3"
          :key="n"
          shape="block"
          height="6rem"
        />
      </div>

      <UiCard
        v-else-if="projectItems.length === 0"
        section
      >
        <UiEmptyState
          icon="cube"
          title="No projects yet"
          description="A project is created when an agent binds this workspace to StackOS."
        />
      </UiCard>

      <ul
        v-else
        class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
      >
        <li
          v-for="project in projectItems"
          :key="project.id"
        >
          <RouterLink
            :to="`/projects/${project.id}`"
            class="focus-ring group flex h-full flex-col rounded-lg border border-default bg-bg-surface p-4 shadow-xs transition-colors duration-fast hover:border-strong hover:bg-bg-surface-alt"
          >
            <div class="flex items-start justify-between gap-2">
              <span class="min-w-0 truncate text-sm font-semibold text-fg-strong">
                {{ project.name }}
              </span>
              <UiBadge
                v-if="!project.is_active"
                tone="neutral"
                size="sm"
              >
                Archived
              </UiBadge>
            </div>
            <span class="mt-1 truncate font-mono text-2xs text-fg-subtle">{{ project.slug }}</span>
            <div class="mt-3 flex items-center justify-between gap-2">
              <span class="truncate text-2xs text-fg-muted">{{ project.domain || '—' }}</span>
              <span class="inline-flex items-center gap-1 text-2xs font-medium text-fg-link">
                Open
                <UiIcon
                  name="arrow-right"
                  class="h-3.5 w-3.5 opacity-0 transition-opacity duration-fast group-hover:opacity-100"
                  aria-hidden="true"
                />
              </span>
            </div>
          </RouterLink>
        </li>
      </ul>
    </section>

    <UiConfirmDialog
      v-model="repairOpen"
      title="Install or repair StackOS?"
      description="This reinstalls local StackOS assets and restarts the service. It’s safe to run, but the service will be briefly unavailable."
      confirm-label="Install or repair"
      cancel-label="Cancel"
      tone="primary"
      @confirm="confirmRepair"
      @cancel="repairOpen = false"
    />
  </UiPageShell>
</template>
