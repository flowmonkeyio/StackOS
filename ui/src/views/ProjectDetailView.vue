<script setup lang="ts">
// ProjectDetailView — shared project setup page chrome.
//
// The sidebar owns navigation. This wrapper only gives each project-scoped
// setup route a focused page title, project meta, and status context.

import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { UiPageShell } from '@/components/ui'
import { useProjectsStore } from '@/stores/projects'

const route = useRoute()
const projects = useProjectsStore()
const { items } = storeToRefs(projects)

const projectId = computed<number>(() => Number.parseInt(route.params.id as string, 10))
const project = computed(() => projects.getById(projectId.value))

// The Home console (project-home) renders its own page chrome, so this shell
// steps aside for it and only wraps the setup-group tabs.
const isHome = computed<boolean>(() => route.name === 'project-home')

const activeKey = computed<string>(() => {
  const name = String(route.name ?? '')
  const match = name.match(/^project-detail-(.+)$/)
  return match ? match[1] : 'overview'
})

type ProjectSetupKey = 'overview' | 'setup' | 'schedules' | 'cost-budget'

interface PageCopy {
  title: string
  description: string
}

type ProjectSetupPageCopy = {
  [Key in ProjectSetupKey]: PageCopy
}

const PAGE_COPY = {
  overview: {
    title: 'Overview',
    description:
      'Project state, enabled plugins, reusable templates, run plans, resources, and recent runs.',
  },
  setup: {
    title: 'Setup',
    description: 'Whether connected agents can use this project safely — and where to finish setup.',
  },
  schedules: {
    title: 'Schedules',
    description: 'Recurring project operations and scheduled maintenance.',
  },
  'cost-budget': {
    title: 'Cost & Budget',
    description: 'Monthly spend, vendor caps, alerts, and request-rate controls.',
  },
} satisfies ProjectSetupPageCopy

function isProjectSetupKey(key: string): key is ProjectSetupKey {
  return key in PAGE_COPY
}

const pageCopy = computed(() =>
  isProjectSetupKey(activeKey.value) ? PAGE_COPY[activeKey.value] : PAGE_COPY.overview,
)
const pageBreadcrumbs = computed(() =>
  activeKey.value === 'overview' ? [] : [{ label: pageCopy.value.title }],
)

// The overview is the project home — lead with the project's own name.
const isOverview = computed(() => activeKey.value === 'overview')
const pageTitle = computed(() =>
  isOverview.value ? project.value?.name ?? pageCopy.value.title : pageCopy.value.title,
)

async function ensureLoaded(): Promise<void> {
  if (project.value) return
  if (items.value.length === 0) await projects.refresh()
}

onMounted(ensureLoaded)
</script>

<template>
  <RouterView v-if="isHome" />
  <UiPageShell v-else>
    <ProjectPageHeader
      :project-id="projectId"
      :title="pageTitle"
      :description="pageCopy.description"
      :breadcrumbs="pageBreadcrumbs"
      :show-project-meta="isOverview"
      show-project-status
    />
    <RouterView />
  </UiPageShell>
</template>
