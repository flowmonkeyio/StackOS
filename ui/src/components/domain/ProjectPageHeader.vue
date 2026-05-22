<!--
  ProjectPageHeader — project-aware page chrome for every project route.
  Centralizes breadcrumbs, page title, subtitle, project meta, and actions.
-->
<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'

import StatusBadge from '@/components/StatusBadge.vue'
import UiBreadcrumbs from '@/components/ui/UiBreadcrumbs.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import { useProjectsStore } from '@/stores/projects'

interface BreadcrumbItem {
  label: string;
  to?: string;
}

const props = withDefaults(defineProps<{
  projectId: number;
  title?: string;
  description?: string;
  breadcrumbs?: BreadcrumbItem[];
  showProjectMeta?: boolean;
  showProjectStatus?: boolean;
  showBreadcrumbs?: boolean;
}>(), {
  title: undefined,
  description: undefined,
  breadcrumbs: () => [],
  showProjectMeta: true,
  showProjectStatus: false,
  showBreadcrumbs: false,
})

const projectsStore = useProjectsStore()
const project = computed(() => projectsStore.getById(props.projectId))

const titleText = computed(() => props.title ?? project.value?.name ?? 'Project')

const breadcrumbItems = computed<BreadcrumbItem[]>(() => [
  { label: 'Projects', to: '/projects' },
  {
    label: project.value?.name ?? `Project ${props.projectId}`,
    to: `/projects/${props.projectId}/overview`,
  },
  ...props.breadcrumbs,
])

async function ensureProject(): Promise<void> {
  if (project.value) return
  await projectsStore.refresh()
}

onMounted(ensureProject)
watch(() => props.projectId, ensureProject)
</script>

<template>
  <UiPageHeader
    :title="titleText"
    :description="description"
    :eyebrow="project?.name"
    :show-breadcrumbs="showBreadcrumbs"
  >
    <template
      v-if="showBreadcrumbs"
      #breadcrumbs
    >
      <UiBreadcrumbs :items="breadcrumbItems" />
    </template>

    <template
      v-if="showProjectStatus || $slots.titleMeta"
      #titleMeta
    >
      <StatusBadge
        v-if="showProjectStatus && project"
        :status="project.is_active ? 'active' : 'inactive'"
        kind="project"
      />
      <slot name="titleMeta" />
    </template>

    <template
      v-if="showProjectMeta || $slots.meta"
      #meta
    >
      <template v-if="showProjectMeta && project">
        <span class="font-mono">{{ project.slug }}</span>
        <span>{{ project.domain }}</span>
        <span>{{ project.locale }}</span>
      </template>
      <slot name="meta" />
    </template>

    <template
      v-if="$slots.actions"
      #actions
    >
      <slot name="actions" />
    </template>

    <template
      v-if="$slots.tabs"
      #tabs
    >
      <slot name="tabs" />
    </template>
  </UiPageHeader>
</template>
