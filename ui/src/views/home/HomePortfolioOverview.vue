<script setup lang="ts">
import { computed } from 'vue'

import {
  UiBadge,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiIcon,
  UiMetricCard,
  UiProgressBar,
  UiSkeleton,
} from '@/components/ui'

import type {
  PortfolioWorkItem,
  ProjectPortfolioInsight,
} from './useHomePortfolioInsights'

const props = defineProps<{
  insights: ProjectPortfolioInsight[]
  activeWork: PortfolioWorkItem[]
  totals: {
    activeProjects: number
    activeTasks: number
    activeTickets: number
    blockers: number
  }
  loading: boolean
  failedProjectCount: number
}>()

const chartProjects = computed(() =>
  [...props.insights]
    .filter((insight) => insight.activeTaskCount > 0 || insight.inProgressTicketCount > 0)
    .sort(
      (a, b) =>
        b.activeTaskCount + b.inProgressTicketCount -
        (a.activeTaskCount + a.inProgressTicketCount),
    ),
)

const maxLoad = computed(() =>
  Math.max(
    1,
    ...chartProjects.value.map(
      (insight) => insight.activeTaskCount + insight.inProgressTicketCount,
    ),
  ),
)

function workloadWidth(insight: ProjectPortfolioInsight): number {
  return Math.max(
    4,
    Math.round(
      ((insight.activeTaskCount + insight.inProgressTicketCount) / maxLoad.value) * 100,
    ),
  )
}

function workMeta(item: PortfolioWorkItem): string {
  const parts = [item.projectName]
  if (item.owner) parts.push(`owned by ${item.owner}`)
  parts.push(
    item.activeTicketCount === 1
      ? '1 active child'
      : `${item.activeTicketCount} active children`,
  )
  return parts.join(' · ')
}
</script>

<template>
  <section
    aria-labelledby="portfolio-operations-heading"
    class="space-y-4"
  >
    <div>
      <div class="flex flex-wrap items-center gap-2">
        <h2
          id="portfolio-operations-heading"
          class="t-h3 text-fg-strong"
        >
          Portfolio operations
        </h2>
        <UiBadge
          tone="neutral"
          size="sm"
        >
          All active projects
        </UiBadge>
      </div>
      <p class="mt-1 text-xs text-fg-muted">
        A live view of work agents are carrying out through StackOS, where it is happening, and what needs attention.
      </p>
    </div>

    <UiCallout
      v-if="failedProjectCount > 0"
      tone="warning"
    >
      {{ failedProjectCount }} project{{ failedProjectCount === 1 ? '' : 's' }} could not be read. The totals below include the projects that responded.
    </UiCallout>

    <div
      v-if="loading && insights.length === 0"
      class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
    >
      <UiSkeleton
        v-for="n in 4"
        :key="n"
        shape="block"
        height="6.5rem"
      />
    </div>

    <div
      v-else
      class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
    >
      <UiMetricCard
        label="Projects with active work"
        :value="totals.activeProjects"
        hint="Projects agents are working in now"
      />
      <UiMetricCard
        label="Work streams in progress"
        :value="totals.activeTasks"
        hint="Top-level tracked goals"
      />
      <UiMetricCard
        label="Active delivery steps"
        :value="totals.activeTickets"
        hint="Concrete work underneath those goals"
      />
      <UiMetricCard
        label="Blocked steps"
        :value="totals.blockers"
        :hint="totals.blockers > 0 ? 'Open Attention to resolve them' : 'Nothing is waiting on a blocker'"
        :value-tone="totals.blockers > 0 ? 'warning' : 'success'"
      />
    </div>

    <div class="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
      <UiCard section>
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 class="text-sm font-semibold text-fg-strong">
              Work in progress
            </h3>
            <p class="mt-1 text-xs text-fg-muted">
              Every active top-level task across the portfolio. Open one to see its delivery steps and evidence.
            </p>
          </div>
          <UiBadge
            tone="info"
            size="sm"
          >
            {{ activeWork.length }} active
          </UiBadge>
        </div>

        <div
          v-if="loading && activeWork.length === 0"
          class="mt-4 space-y-2"
        >
          <UiSkeleton
            v-for="n in 3"
            :key="n"
            shape="block"
            height="4.5rem"
          />
        </div>

        <UiEmptyState
          v-else-if="activeWork.length === 0"
          class="mt-4"
          icon="check-circle"
          title="No work is currently in progress"
          description="When an agent starts tracked work, the task and its project will appear here."
          size="sm"
        />

        <ul
          v-else
          class="mt-4 divide-y divide-border-subtle overflow-hidden rounded-lg border border-subtle"
        >
          <li
            v-for="item in activeWork"
            :key="`${item.projectId}:${item.key}`"
          >
            <RouterLink
              :to="`/projects/${item.projectId}/tasks?task=${encodeURIComponent(item.key)}`"
              class="focus-ring-inset group grid gap-3 px-4 py-3 transition-colors hover:bg-bg-surface-alt sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
            >
              <span class="min-w-0">
                <span class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-semibold text-fg-strong">{{ item.title }}</span>
                  <UiBadge
                    v-if="item.blockerCount > 0"
                    tone="warning"
                    size="sm"
                  >
                    {{ item.blockerCount }} blocked
                  </UiBadge>
                </span>
                <span class="mt-1 block text-xs text-fg-muted">{{ workMeta(item) }}</span>
              </span>
              <span class="inline-flex items-center gap-1 text-xs font-semibold text-fg-link">
                View work
                <UiIcon
                  name="arrow-right"
                  class="h-3.5 w-3.5"
                  aria-hidden="true"
                />
              </span>
            </RouterLink>
          </li>
        </ul>
      </UiCard>

      <UiCard section>
        <h3 class="text-sm font-semibold text-fg-strong">
          Workload by project
        </h3>
        <p class="mt-1 text-xs text-fg-muted">
          Relative active tasks and delivery steps. Exact counts are always shown with each bar.
        </p>

        <div
          v-if="chartProjects.length > 0"
          class="mt-5 space-y-4"
        >
          <RouterLink
            v-for="project in chartProjects"
            :key="project.projectId"
            :to="`/projects/${project.projectId}/tasks`"
            class="focus-ring group block rounded-md"
          >
            <span class="flex items-center justify-between gap-3 text-xs">
              <span class="truncate font-medium text-fg-strong">{{ project.projectName }}</span>
              <span class="shrink-0 text-fg-muted">
                {{ project.activeTaskCount }} task{{ project.activeTaskCount === 1 ? '' : 's' }} · {{ project.inProgressTicketCount }} step{{ project.inProgressTicketCount === 1 ? '' : 's' }}
              </span>
            </span>
            <span
              class="mt-2 block h-2 overflow-hidden rounded-full bg-bg-inset"
              aria-hidden="true"
            >
              <span
                class="block h-full rounded-full bg-accent transition-all"
                :style="{ width: `${workloadWidth(project)}%` }"
              />
            </span>
            <span class="mt-2 flex items-center justify-between gap-3 text-2xs text-fg-subtle">
              <span>{{ project.completionPercent }}% of tracked tasks closed</span>
              <span v-if="project.blockedTicketCount > 0">{{ project.blockedTicketCount }} blocked</span>
            </span>
          </RouterLink>
        </div>

        <UiEmptyState
          v-else
          class="mt-4"
          icon="chart-bar"
          title="No active workload"
          description="Project workload appears here as agents start tracked work."
          size="sm"
        />

        <div
          v-if="chartProjects.length > 0"
          class="mt-5 border-t border-border-subtle pt-4"
        >
          <p class="text-2xs font-medium uppercase tracking-wide text-fg-subtle">
            Portfolio completion
          </p>
          <div class="mt-2 space-y-3">
            <div
              v-for="project in chartProjects.slice(0, 5)"
              :key="`progress:${project.projectId}`"
              class="space-y-1"
            >
              <div class="flex justify-between gap-3 text-2xs text-fg-muted">
                <span class="truncate">{{ project.projectName }}</span>
                <span>{{ project.doneTaskCount }}/{{ project.totalTaskCount }}</span>
              </div>
              <UiProgressBar
                :value="project.completionPercent"
                :aria-label="`${project.projectName} task completion`"
                size="sm"
              />
            </div>
          </div>
        </div>
      </UiCard>
    </div>
  </section>
</template>
