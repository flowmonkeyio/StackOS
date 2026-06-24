<!--
  ProjectPageHeader — project-aware page chrome for every project route.
  Centralizes breadcrumbs, page title, subtitle, project meta, and actions.
-->
<script setup lang="ts">
import { computed, onMounted } from 'vue'

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
  /** Opt-in slug/domain/locale chips. The switcher already shows project context. */
  showProjectMeta?: boolean;
  showProjectStatus?: boolean;
  showBreadcrumbs?: boolean;
}>(), {
  title: undefined,
  description: undefined,
  breadcrumbs: () => [],
  showProjectMeta: false,
  showProjectStatus: false,
  showBreadcrumbs: false,
})

const projectsStore = useProjectsStore()
const project = computed(() => projectsStore.getById(props.projectId))

const titleText = computed(() => props.title ?? project.value?.name ?? 'Project')

const breadcrumbItems = computed<BreadcrumbItem[]>(() => [
  {
    label: project.value?.name ?? `Project ${props.projectId}`,
    to: `/projects/${props.projectId}`,
  },
  ...props.breadcrumbs,
])

async function ensureProject(): Promise<void> {
  if (project.value) return
  await projectsStore.refresh()
}

onMounted(ensureProject)
</script>

<template>
  <UiPageHeader
    :title="titleText"
    :description="description"
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
        v-if="showProjectStatus && project && !project.is_active"
        status="archived"
        kind="project"
      />
      <slot name="titleMeta" />
    </template>

    <template
      v-if="showProjectMeta || $slots.meta"
      #meta
    >
      <template v-if="showProjectMeta && project">
        <span class="inline-flex items-center rounded-full border border-default bg-bg-surface px-2 py-0.5 font-mono text-2xs text-fg-muted">{{ project.slug }}</span>
        <span class="inline-flex items-center rounded-full border border-default bg-bg-surface px-2 py-0.5 text-2xs text-fg-muted">{{ project.domain }}</span>
        <span class="inline-flex items-center rounded-full border border-default bg-bg-surface px-2 py-0.5 text-2xs text-fg-muted">{{ project.locale }}</span>
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
