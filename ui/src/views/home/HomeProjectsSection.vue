<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  UiBadge,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiIcon,
  UiInput,
  UiSegmentedControl,
  UiSkeleton,
} from '@/components/ui'
import { formatRelativeDateTime } from '@/lib/stackos/time'
import type { Project } from '@/stores/projects'

import type { ProjectPortfolioInsight } from './useHomePortfolioInsights'

const props = defineProps<{
  items: Project[]
  loading: boolean
  error: string | null
  currentProjectId?: number | null
  insights: Record<number, ProjectPortfolioInsight>
}>()

const search = ref('')
const filter = ref<'active' | 'archived' | 'all'>('active')
const filterOptions = computed(() => [
  { key: 'active', label: `Active ${props.items.filter((project) => project.is_active).length}` },
  { key: 'archived', label: `Archived ${props.items.filter((project) => !project.is_active).length}` },
  { key: 'all', label: `All ${props.items.length}` },
])

const visibleProjects = computed(() => {
  const needle = search.value.trim().toLowerCase()
  return props.items
    .filter((project) => {
      if (filter.value === 'active' && !project.is_active) return false
      if (filter.value === 'archived' && project.is_active) return false
      if (!needle) return true
      return [project.name, project.slug, project.domain].some((value) =>
        String(value ?? '').toLowerCase().includes(needle),
      )
    })
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
})

const featuredProject = computed(() =>
  filter.value === 'active' && search.value.trim() === ''
    ? visibleProjects.value.find((project) => project.id === props.currentProjectId) ??
      visibleProjects.value[0] ??
      null
    : null,
)
const listedProjects = computed(() =>
  featuredProject.value
    ? visibleProjects.value.filter((project) => project.id !== featuredProject.value?.id)
    : visibleProjects.value,
)

function projectUpdated(project: Project): string {
  return formatRelativeDateTime(project.updated_at)
}

function projectState(project: Project): { label: string; tone: 'info' | 'warning' | 'neutral' } {
  const insight = props.insights[project.id]
  if (!project.is_active) return { label: 'Archived', tone: 'neutral' }
  if (insight?.blockedTicketCount) {
    return { label: `${insight.blockedTicketCount} blocked`, tone: 'warning' }
  }
  if (insight && (insight.activeTaskCount > 0 || insight.inProgressTicketCount > 0)) {
    return {
      label: `${insight.activeTaskCount} open task${insight.activeTaskCount === 1 ? '' : 's'}`,
      tone: 'info',
    }
  }
  return { label: 'No open work', tone: 'neutral' }
}
</script>

<template>
  <section
    aria-label="Projects"
    class="space-y-3"
  >
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <div class="flex items-center gap-2">
          <h2 class="t-h3 text-fg-strong">
            Projects
          </h2>
          <UiBadge
            tone="neutral"
            size="sm"
          >
            {{ items.length }}
          </UiBadge>
        </div>
        <p class="mt-1 text-xs text-fg-muted">
          Choose the project whose setup, work, and audit state you want to supervise.
        </p>
      </div>
      <div class="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:justify-end">
        <UiSegmentedControl
          v-model="filter"
          :options="filterOptions"
          label="Project visibility"
        />
        <UiInput
          v-if="items.length > 4"
          v-model="search"
          class="min-w-52 flex-1 sm:w-64 sm:flex-none"
          size="sm"
          placeholder="Find a project"
          aria-label="Find a project"
        />
      </div>
    </div>

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div
      v-if="loading && items.length === 0"
      class="space-y-2"
    >
      <UiSkeleton
        v-for="n in 4"
        :key="n"
        shape="block"
        height="3.75rem"
      />
    </div>

    <UiCard
      v-else-if="items.length === 0"
      section
    >
      <UiEmptyState
        icon="cube"
        title="No projects yet"
        description="A project appears after an agent binds a deliberate workspace to StackOS through MCP."
      />
    </UiCard>

    <UiCard
      v-else-if="visibleProjects.length === 0"
      section
    >
      <UiEmptyState
        icon="search"
        :title="filter === 'archived' ? 'No archived projects' : 'No matching projects'"
        :description="filter === 'archived' ? 'Archived projects will stay available here without cluttering active work.' : 'Try another name, slug, or domain, or change the visibility filter.'"
        size="sm"
      />
    </UiCard>

    <RouterLink
      v-if="featuredProject"
      :to="`/projects/${featuredProject.id}`"
      class="focus-ring group grid gap-4 rounded-xl border border-strong bg-bg-surface p-5 shadow-sm transition hover:bg-bg-surface-alt sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center"
    >
      <span
        class="inline-flex h-12 w-12 items-center justify-center rounded-lg bg-accent-subtle text-base font-semibold text-accent-fg"
        aria-hidden="true"
      >
        {{ featuredProject.name.slice(0, 2).toUpperCase() }}
      </span>
      <span class="min-w-0">
        <span class="t-overline text-accent-fg">Current workspace</span>
        <span class="mt-1 block truncate text-lg font-semibold text-fg-strong">
          {{ featuredProject.name }}
        </span>
        <span class="mt-1 block text-xs text-fg-muted">
          {{ featuredProject.domain || 'No domain' }} · updated {{ projectUpdated(featuredProject) }}
        </span>
        <span
          v-if="insights[featuredProject.id]"
          class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted"
        >
          <span>{{ insights[featuredProject.id].activeTaskCount }} open tasks</span>
          <span>{{ insights[featuredProject.id].inProgressTicketCount }} delivery steps</span>
          <span>{{ insights[featuredProject.id].completionPercent }}% closed</span>
        </span>
      </span>
      <span class="inline-flex items-center gap-1.5 text-sm font-semibold text-fg-link">
        Open project
        <UiIcon
          name="arrow-right"
          class="h-4 w-4"
          aria-hidden="true"
        />
      </span>
    </RouterLink>

    <div
      v-if="visibleProjects.length > 0"
      class="overflow-hidden rounded-lg border border-subtle bg-bg-surface"
    >
      <div class="grid grid-cols-[minmax(0,1fr)_auto] border-b border-border-subtle bg-bg-surface-alt px-4 py-2 text-2xs font-medium uppercase tracking-wide text-fg-subtle sm:grid-cols-[minmax(0,1fr)_10rem_7rem]">
        <span>Project</span>
        <span class="hidden sm:block">Last updated</span>
        <span class="text-right">State</span>
      </div>
      <ul class="divide-y divide-border-subtle">
        <li
          v-for="project in listedProjects"
          :key="project.id"
        >
          <RouterLink
            :to="`/projects/${project.id}`"
            class="focus-ring-inset group grid min-h-16 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 px-4 py-3 transition-colors duration-fast hover:bg-bg-surface-alt sm:grid-cols-[auto_minmax(0,1fr)_10rem_7rem]"
          >
            <span
              class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-accent-subtle text-sm font-semibold text-accent-fg"
              aria-hidden="true"
            >
              {{ project.name.slice(0, 2).toUpperCase() }}
            </span>
            <span class="min-w-0">
              <span class="flex flex-wrap items-center gap-2">
                <span class="truncate text-sm font-semibold text-fg-strong">{{ project.name }}</span>
              </span>
              <span class="mt-0.5 block truncate text-xs text-fg-muted">
                {{ project.domain || 'No domain' }} · {{ project.slug }}
              </span>
            </span>
            <span class="hidden text-xs text-fg-muted sm:block">
              {{ projectUpdated(project) }}
            </span>
            <span class="flex justify-end">
              <UiBadge
                :tone="projectState(project).tone"
                size="sm"
              >
                {{ projectState(project).label }}
              </UiBadge>
            </span>
          </RouterLink>
        </li>
      </ul>
      <div
        v-if="listedProjects.length === 0"
        class="px-4 py-5 text-center text-xs text-fg-muted"
      >
        No other active projects.
      </div>
    </div>
  </section>
</template>
