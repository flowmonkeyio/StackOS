<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { SchemaLoadedWorkflowTemplate } from '@/api'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import TemplateRenderer from '@/components/renderers/TemplateRenderer.vue'
import { UiButton, UiCallout, UiPageShell, UiSkeleton } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { ApiError, apiFetch, formatApiError } from '@/lib/client'
import { projectIdFromRoute } from '@/lib/stackos/projectNavigation'
import { useStackOsCatalogStore } from '@/stores/plugins'
import { domainLocation } from './plugin-domain/viewModel'

const route = useRoute()
const router = useRouter()
const { projectId } = useProjectRouteScope(route)
const slug = computed(() => String(route.params.pluginSlug ?? ''))
const key = computed(() => String(route.params.templateKey ?? ''))
const catalog = useStackOsCatalogStore()
const acceptedTemplate = ref<SchemaLoadedWorkflowTemplate | null>(null)
const templateScope = ref<() => boolean>(() => false)
const plugin = computed(() => catalog.pluginProjectId === projectId.value
  ? catalog.plugins.find(item => item.slug === slug.value && item.enabled_for_project !== false) : undefined)
const template = computed(() => !slug.value || (plugin.value && templateScope.value()) ? acceptedTemplate.value : null)
const loadError = ref('')
const error = computed(() => (slug.value ? catalog.pluginError : null) ?? loadError.value)
const loading = ref(true)
let generation = 0
async function load(nextProjectId = projectId.value, nextSlug = slug.value, nextKey = key.value) {
  const current = ++generation
  loadError.value = ''
  loading.value = true
  if (!Number.isSafeInteger(nextProjectId) || nextProjectId <= 0 || !nextKey) {
    acceptedTemplate.value = null
    loadError.value = 'This workflow identity is invalid.'
    loading.value = false
    return
  }
  if (nextSlug) {
    const ready = await catalog.refreshPlugins(nextProjectId)
    if (current !== generation) return
    if (!ready) {
      loadError.value = catalog.pluginError ?? 'Plugin availability could not be refreshed.'
    } else if (!catalog.plugins.some(item => item.slug === nextSlug && item.enabled_for_project !== false)) {
      loadError.value = 'This plugin is unavailable or disabled for this project.'
      acceptedTemplate.value = null
    }
  }
  if (!error.value) {
    const isCurrentScope = nextSlug ? catalog.capturePluginScope(nextProjectId) : () => true
    try {
      const suffix = nextSlug ? `?plugin_slug=${encodeURIComponent(nextSlug)}` : ''
      const result = await apiFetch<SchemaLoadedWorkflowTemplate>(`/api/v1/projects/${nextProjectId}/workflow-templates/${encodeURIComponent(nextKey)}${suffix}`)
      if (current !== generation) return
      if (!isCurrentScope()) { loading.value = false; return }
      if (result.summary.key !== nextKey || (result.summary.plugin_slug ?? '') !== nextSlug || result.spec.key !== nextKey) {
        acceptedTemplate.value = null
        throw new Error('The workflow source does not match this page.')
      }
      acceptedTemplate.value = result
      templateScope.value = isCurrentScope
    } catch (err) {
      if (current === generation && isCurrentScope()) {
        loadError.value = formatApiError(err, 'Workflow unavailable')
        if (err instanceof ApiError && [400, 401, 403, 404, 410, 422].includes(err.status)) acceptedTemplate.value = null
      }
    }
  }
  if (current === generation) {
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
    acceptedTemplate.value = null
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
    <div
      v-if="loading && !template"
      role="status"
      aria-label="Loading workflow"
      aria-busy="true"
      class="space-y-4"
    >
      <p class="min-h-5 text-xs text-fg-muted">Loading workflow</p>
      <div class="workflow-loading" aria-hidden="true">
        <div class="space-y-4">
          <UiSkeleton width="7rem" :height="20" />
          <div class="overflow-hidden rounded-lg border border-default bg-bg-surface">
            <div v-for="stage in 3" :key="stage" class="flex items-center gap-5 border-b border-subtle px-5 py-3 last:border-b-0">
              <UiSkeleton shape="block" :width="28" :height="28" />
              <UiSkeleton width="55%" />
            </div>
          </div>
        </div>
        <div class="min-w-0 space-y-6">
          <div v-for="section in 2" :key="section" class="space-y-4">
            <UiSkeleton width="9rem" :height="20" />
            <div class="rounded-lg border border-default bg-bg-surface p-4">
              <UiSkeleton :lines="3" />
            </div>
          </div>
        </div>
      </div>
    </div>
    <div v-if="template" class="min-h-5 text-xs text-fg-muted" role="status">
      <details v-if="error">
        <summary class="focus-ring cursor-pointer rounded-sm">Last accepted workflow · refresh required</summary>
        <UiCallout tone="warning" class="mt-2">
          {{ error }} Inspection only. <UiButton size="sm" @click="load()">Retry</UiButton>
        </UiCallout>
      </details>
      <p v-else>{{ loading ? 'Refreshing workflow…' : 'Workflow contract · inspection only.' }}</p>
    </div>
    <UiCallout
      v-if="error && !template"
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
      v-if="template"
      :template="template"
    />
  </UiPageShell>
</template>

<style scoped>
.workflow-page :deep(.ui-page-header h1) { white-space: normal; overflow-wrap: anywhere; }
.workflow-loading { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); gap: 32px; }
@media (max-width: 900px) { .workflow-loading { grid-template-columns: minmax(0, 1fr); gap: 24px; } }
</style>
