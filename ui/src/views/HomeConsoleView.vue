<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ProjectPageHeader } from '@/components/domain'
import ProviderMark from '@/components/domain/ProviderMark.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiButton,
  UiCallout,
  UiCard,
  UiIcon,
  UiPageShell,
  UiSectionHeader,
  UiSkeleton,
} from '@/components/ui'
import type { TrackerStatus } from '@/lib/task-tracker/types'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { usePolling } from '@/composables/usePolling'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import { connectionNeedsAttention, connectionStatusKey } from './connections/credentialPresentation'
import { providerLabel } from './connections/formatters'
import TicketStatusChart from './home/TicketStatusChart.vue'
import { useTicketCounts } from './home/useTicketCounts'
import { useProjectOverview } from './home/useProjectOverview'
import { ticketWorkUrl } from './home/ticketOverview'

const { projectId } = useProjectRouteScope()
const router = useRouter()
const base = computed(() => `/projects/${projectId.value}`)
const overview = useProjectOverview(projectId)
const { project, accounts, workflows, task, loading, errors } = overview
const description = computed(() => project.value?.domain || 'Current project work')
const tickets = useTicketCounts(projectId)
const { summary, loading: countsLoading, error: countsError } = tickets
const { running, refresh } = usePolling(
  async () => {
    await Promise.all([overview.load(), tickets.load()])
  },
  { intervalMs: 30_000 },
)
function inspectStatus(status: TrackerStatus): void {
  void router.push(ticketWorkUrl(projectId.value, status))
}
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      :title="project?.name"
      :description="description"
    >
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="running"
          @click="refresh"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>
    <div class="grid items-start gap-4 xl:grid-cols-[minmax(0,7fr)_minmax(16rem,3fr)]">
      <UiCard
        section
        aria-label="Tickets by status"
      >
        <UiSectionHeader
          title="Tickets by status"
          :description="summary ? `Current snapshot · ${summary.total_count} ${summary.total_count === 1 ? 'ticket' : 'tickets'}` : 'Current ticket status for this project'"
        >
          <template #actions>
            <UiButton
              :href="ticketWorkUrl(projectId)"
              variant="ghost"
              size="sm"
              icon-right="arrow-right"
              @click.prevent="router.push(ticketWorkUrl(projectId))"
            >
              View all tickets
            </UiButton>
          </template>
        </UiSectionHeader>
        <UiCallout
          v-if="countsError"
          tone="warning"
          class="mt-3"
        >
          {{ countsError }}<span v-if="summary"> Previously loaded counts are shown.</span>
        </UiCallout>
        <UiSkeleton
          v-if="countsLoading && !summary"
          shape="block"
          height="8rem"
          class="mt-3"
        />
        <TicketStatusChart
          v-else-if="summary"
          :counts="summary.ticket_counts"
          :total="summary.total_count"
          class="mt-4"
          @select="inspectStatus"
        />
        <p
          v-if="summary"
          class="mt-3 border-t border-subtle pt-3 text-2xs text-fg-subtle"
        >
          Updated {{ formatAbsoluteDateTime(summary.as_of) }}
        </p>
      </UiCard>
      <div class="space-y-4">
        <UiCard
          section
          aria-label="Project connections"
        >
          <UiSectionHeader title="Project connections" />
          <UiCallout
            v-if="errors.connections"
            tone="warning"
            class="mt-3"
          >
            {{ errors.connections }}
          </UiCallout>
          <UiSkeleton
            v-if="loading"
            shape="block"
            height="6rem"
            class="mt-3"
          />
          <ul
            v-else-if="accounts.length"
            class="mt-3 divide-y divide-border-subtle"
          >
            <li
              v-for="account in accounts"
              :key="account.credential_ref"
              class="flex min-w-0 items-center gap-2 py-2"
            >
              <ProviderMark
                :provider-key="account.provider_key"
                :name="providerLabel(account.provider_key)"
                size="xs"
              />
              <div class="min-w-0 flex-1">
                <p class="truncate text-sm text-fg-strong">
                  {{ providerLabel(account.provider_key) }}
                </p>
                <p
                  class="truncate text-2xs text-fg-muted"
                  :title="account.display_name"
                >
                  {{ account.display_name }}
                </p>
              </div>
              <StatusBadge
                kind="connection"
                :status="connectionStatusKey(account)"
                :label="
                  connectionNeedsAttention(account) && account.last_test?.ok === false
                    ? 'Test failed'
                    : undefined
                "
                :tone="account.last_test?.ok === false ? 'danger' : undefined"
              />
            </li>
          </ul>
          <p
            v-else-if="!errors.connections"
            class="my-4 text-xs text-fg-muted"
          >
            No Accounts are attached to this project.
          </p>
          <UiButton
            :href="`${base}/connections?section=services`"
            variant="ghost"
            size="sm"
            icon-right="chevron-right"
            class="mt-3"
            @click.prevent="router.push(`${base}/connections?section=services`)"
          >
            Manage connections
          </UiButton>
        </UiCard>
        <UiCard
          section
          aria-label="Configured workflows"
        >
          <UiSectionHeader title="Configured workflows" />
          <UiCallout
            v-if="errors.workflows"
            tone="warning"
            class="mt-3"
          >
            {{ errors.workflows }}
          </UiCallout>
          <UiSkeleton
            v-if="loading"
            shape="block"
            height="4rem"
            class="mt-3"
          />
          <ul
            v-else-if="workflows.length"
            class="mt-2 space-y-1"
          >
            <li
              v-for="workflow in workflows"
              :key="workflow.key"
            >
              <RouterLink
                v-slot="{ href, navigate }"
                :to="`${base}/workflow-templates${workflow.pluginSlug ? `?plugin_slug=${encodeURIComponent(workflow.pluginSlug)}` : ''}`"
                custom
              >
                <UiButton
                  :href="href"
                  variant="ghost"
                  size="sm"
                  icon-left="list"
                  :title="workflow.key"
                  @click="navigate"
                >
                  {{ workflow.name }}
                </UiButton>
              </RouterLink>
            </li>
          </ul>
          <p
            v-else-if="!errors.workflows"
            class="my-4 text-xs text-fg-muted"
          >
            No enabled project workflow configuration is recorded.
          </p>
          <UiButton
            :href="`${base}/workflow-templates`"
            variant="ghost"
            size="sm"
            icon-right="arrow-right"
            class="mt-3"
            @click.prevent="router.push(`${base}/workflow-templates`)"
          >
            View workflow library
          </UiButton>
        </UiCard>
      </div>
    </div>
    <div
      class="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-default bg-bg-surface px-4 py-3"
      aria-label="Tracked work"
    >
      <span class="flex items-center gap-2 text-xs text-fg-muted"><UiIcon
        name="list"
        :size="16"
      />Tracked work</span>
      <UiCallout
        v-if="errors.work"
        tone="warning"
      >
        {{ errors.work }}
      </UiCallout>
      <template v-else-if="task">
        <span class="text-sm text-fg-strong">{{ task.title }}</span><StatusBadge
          kind="tracker"
          :status="task.status"
        /><span class="text-xs text-fg-subtle">Recorded {{ formatAbsoluteDateTime(task.updated_at) }}</span><UiButton
          :href="`${base}/tasks?task=${encodeURIComponent(task.key)}`"
          variant="ghost"
          size="sm"
          icon-right="arrow-right"
          @click.prevent="router.push(`${base}/tasks?task=${encodeURIComponent(task.key)}`)"
        >
          Open Work
        </UiButton>
      </template>
      <span
        v-else-if="!loading"
        class="text-xs text-fg-muted"
      >No tracked work yet. Work starts in your connected AI tool.</span>
      <UiButton
        :href="`${base}/tasks`"
        variant="ghost"
        size="sm"
        class="ml-auto"
        @click.prevent="router.push(`${base}/tasks`)"
      >
        View Work
      </UiButton>
    </div>
  </UiPageShell>
</template>
