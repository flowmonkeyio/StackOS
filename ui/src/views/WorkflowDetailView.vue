<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { SchemaLoadedWorkflowTemplate } from '@/api'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import TemplateRenderer from '@/components/renderers/TemplateRenderer.vue'
import { UiButton, UiCallout, UiLoadingState, UiPageShell } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { apiFetch, formatApiError } from '@/lib/client'
import { projectIdFromRoute } from '@/lib/stackos/projectNavigation'
import { useStackOsCatalogStore } from '@/stores/plugins'
import { domainLocation } from './plugin-domain/viewModel'

const route = useRoute()
const router = useRouter()
const { projectId } = useProjectRouteScope(route)
const slug = computed(() => String(route.params.pluginSlug ?? ''))
const key = computed(() => String(route.params.templateKey ?? ''))
const catalog = useStackOsCatalogStore()
const template = ref<SchemaLoadedWorkflowTemplate | null>(null)
const error = ref('')
const loading = ref(true)
let generation = 0
async function load(nextProjectId = projectId.value, nextSlug = slug.value, nextKey = key.value) {
  const current = ++generation
  error.value = ''
  loading.value = true
  if (!Number.isSafeInteger(nextProjectId)) { loading.value = false; return }
  if (nextSlug) {
    await catalog.refreshPlugins(nextProjectId)
    if (current !== generation) return
    if (catalog.error) error.value = catalog.error
    else if (!catalog.plugins.some(item => item.slug === nextSlug && item.enabled_for_project !== false)) error.value = 'This plugin is unavailable or disabled for this project.'
  }
  if (!error.value) {
    try {
      const suffix = nextSlug ? `?plugin_slug=${encodeURIComponent(nextSlug)}` : ''
      const result = await apiFetch<SchemaLoadedWorkflowTemplate>(`/api/v1/projects/${nextProjectId}/workflow-templates/${encodeURIComponent(nextKey)}${suffix}`)
      if (current !== generation) return
      if (result.summary.key !== nextKey || (result.summary.plugin_slug ?? '') !== nextSlug || result.spec.key !== nextKey) throw new Error('The workflow source does not match this page.')
      template.value = result
    } catch (err) { if (current === generation) error.value = formatApiError(err, 'Workflow unavailable') }
  }
  if (current === generation) {
    if (error.value) template.value = null
    loading.value = false
  }
}
const workflowRoute = route.matched[route.matched.length - 1]
const removeRouteListener = router.afterEach((to, from, failure) => {
  if (failure) return
  if (!workflowRoute || !to.matched.some(record => record.components?.default === workflowRoute.components?.default)) { generation++; return }
  const nextProjectId = projectIdFromRoute(to) ?? Number.NaN
  const nextSlug = String(to.params.pluginSlug ?? '')
  const nextKey = String(to.params.templateKey ?? '')
  if (nextProjectId !== projectIdFromRoute(from) || nextSlug !== String(from.params.pluginSlug ?? '') || nextKey !== String(from.params.templateKey ?? '')) {
    template.value = null
    void load(nextProjectId, nextSlug, nextKey)
  }
})
void load()
onBeforeUnmount(() => { generation++; removeRouteListener() })
</script>

<template>
  <UiPageShell class="workflow-page">
    <RouterLink
      v-slot="{ href, navigate }"
      :to="slug ? domainLocation(projectId, slug) : `/projects/${projectId}`"
      custom
    >
      <UiButton
        :href="href"
        variant="link"
        size="sm"
        icon-left="chevron-left"
        class="self-start"
        @click="navigate"
      >
        {{ slug ? 'Back to workflows' : 'Back to Home' }}
      </UiButton>
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
          @click="load()"
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
        @click="load()"
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
