<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import type { SchemaLoadedWorkflowTemplate } from '@/api'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import TemplateRenderer from '@/components/renderers/TemplateRenderer.vue'
import { UiButton, UiCallout, UiLoadingState, UiPageShell } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { apiFetch, formatApiError } from '@/lib/client'
import { useStackOsCatalogStore } from '@/stores/plugins'
import { domainLocation } from './plugin-domain/viewModel'

const route = useRoute()
const { projectId, isValid } = useProjectRouteScope(route)
const slug = computed(() => String(route.params.pluginSlug ?? ''))
const key = computed(() => String(route.params.templateKey ?? ''))
const catalog = useStackOsCatalogStore()
const plugin = computed(() => catalog.pluginProjectId === projectId.value ? catalog.plugins.find(item => item.slug === slug.value) : undefined)
const template = ref<SchemaLoadedWorkflowTemplate | null>(null)
const error = ref('')
const loading = ref(true)
let generation = 0
async function load() {
  const current = ++generation
  error.value = ''
  loading.value = true
  if (!isValid.value) { loading.value = false; return }
  await catalog.refreshPlugins(projectId.value)
  if (current !== generation) return
  if (catalog.error) error.value = catalog.error
  else if (!plugin.value || plugin.value.enabled_for_project === false) error.value = 'This plugin is unavailable or disabled for this project.'
  else {
    try {
      const result = await apiFetch<SchemaLoadedWorkflowTemplate>(`/api/v1/projects/${projectId.value}/workflow-templates/${encodeURIComponent(key.value)}?plugin_slug=${encodeURIComponent(slug.value)}`)
      if (current !== generation) return
      if (result.summary.key !== key.value || result.summary.plugin_slug !== slug.value || result.spec.key !== key.value) throw new Error('The workflow source does not match this page.')
      template.value = result
    } catch (err) { if (current === generation) error.value = formatApiError(err, 'Workflow unavailable') }
  }
  if (current === generation) {
    if (error.value) template.value = null
    loading.value = false
  }
}
watch([projectId, slug, key], () => { template.value = null; void load() }, { immediate: true })
onBeforeUnmount(() => { generation++ })
</script>

<template>
  <UiPageShell class="workflow-page">
    <RouterLink
      class="focus-ring w-fit rounded-sm text-sm text-fg-muted"
      :to="domainLocation(projectId, slug)"
    >
      {{ plugin?.name ?? slug }} / Workflows
    </RouterLink>
    <ProjectPageHeader
      :project-id="projectId"
      :title="template?.spec.name ?? 'Workflow'"
      :description="template?.spec.description"
    >
      <template #actions>
        <UiButton
          icon-left="refresh"
          :loading="loading"
          @click="load"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>
    <UiLoadingState
      v-if="loading && !template"
      label="Loading workflow"
    />
    <UiCallout
      v-else-if="error"
      tone="danger"
    >
      {{ error }} <UiButton
        size="sm"
        @click="load"
      >
        Retry
      </UiButton>
    </UiCallout>
    <TemplateRenderer
      v-else-if="template"
      :template="template"
    />
  </UiPageShell>
</template>

<style scoped>
.workflow-page :deep(.ui-page-header h1) { white-space: normal; overflow-wrap: anywhere; }
</style>
