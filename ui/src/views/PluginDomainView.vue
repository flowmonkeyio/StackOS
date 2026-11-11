<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { SchemaPluginOut, SchemaWorkflowTemplateSummaryOut } from '@/api'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { UiButton, UiCallout, UiLoadingState, UiPageShell, UiSectionHeader } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { apiFetch, formatApiError } from '@/lib/client'
import { projectIdFromRoute } from '@/lib/stackos/projectNavigation'
import { useStackOsCatalogStore } from '@/stores/plugins'
import DomainContentSection from './plugin-domain/DomainContentSection.vue'
import { domainPage, workflowLocation } from './plugin-domain/viewModel'

const route = useRoute()
const router = useRouter()
const { projectId } = useProjectRouteScope(route)
const slug = computed(() => String(route.params.pluginSlug ?? ''))
const catalog = useStackOsCatalogStore()
const plugin = ref<SchemaPluginOut>()
const enabled = computed(() => plugin.value && plugin.value.enabled_for_project !== false)
const page = computed(() => domainPage(plugin.value))
const contentSections = computed(() => page.value.sections.filter((section): section is 'saved' | 'files' => section !== 'workflows'))
const workflows = ref<SchemaWorkflowTemplateSummaryOut[]>([])
const loading = ref(true)
const initialized = ref(false)
const error = ref('')
const contentReaders = ref<InstanceType<typeof DomainContentSection>[]>([])
let generation = 0
async function load(nextProjectId = projectId.value, nextSlug = slug.value) {
  const current = ++generation
  const mountedReaders = [...contentReaders.value]
  loading.value = true
  error.value = ''
  if (!Number.isSafeInteger(nextProjectId)) { loading.value = false; return }
  await catalog.refreshPlugins(nextProjectId)
  if (current !== generation) return
  if (!catalog.error) plugin.value = catalog.plugins.find(item => item.slug === nextSlug)
  if (catalog.error) error.value = catalog.error
  else if (enabled.value && page.value.sections.includes('workflows')) {
    try {
      const result = await apiFetch<{ templates: SchemaWorkflowTemplateSummaryOut[] }>(`/api/v1/projects/${nextProjectId}/workflow-templates?plugin_slug=${encodeURIComponent(nextSlug)}`)
      if (current === generation) workflows.value = result.templates.filter(item => item.plugin_slug === nextSlug)
    } catch (err) { if (current === generation) error.value = formatApiError(err, 'Failed to load workflows') }
  }
  if (current !== generation) return
  loading.value = false
  initialized.value = true
  await nextTick()
  if (current !== generation) return
  // New sections initialize themselves. Refresh only the retained instances,
  // preserving their filters and reading state without a prop observer.
  await Promise.all(contentReaders.value.filter(reader => mountedReaders.includes(reader)).map(reader => reader.refresh()))
}
function enterScope(nextProjectId: number, nextSlug: string) {
  plugin.value = undefined
  workflows.value = []
  initialized.value = false
  void load(nextProjectId, nextSlug)
}
const domainRoute = route.matched[route.matched.length - 1]
const removeRouteListener = router.afterEach((to, from, failure) => {
  if (failure) return
  if (!domainRoute || !to.matched.includes(domainRoute)) { generation++; return }
  const nextProjectId = projectIdFromRoute(to) ?? Number.NaN
  const nextSlug = String(to.params.pluginSlug ?? '')
  if (nextProjectId !== projectIdFromRoute(from) || nextSlug !== String(from.params.pluginSlug ?? '')) enterScope(nextProjectId, nextSlug)
})
enterScope(projectId.value, slug.value)
onBeforeUnmount(() => { generation++; removeRouteListener() })
</script>

<template>
  <UiPageShell class="domain-page">
    <ProjectPageHeader
      :project-id="projectId"
      :title="plugin?.name ?? 'Domain'"
      :description="page.description"
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
    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }} <UiButton
        size="sm"
        @click="load()"
      >
        Retry
      </UiButton>
    </UiCallout>
    <UiLoadingState
      v-if="loading && !initialized"
      label="Loading domain"
    />
    <UiCallout
      v-else-if="!enabled"
      tone="neutral"
    >
      {{ plugin ? 'This plugin is disabled for this project.' : 'This plugin is unavailable for this project.' }}
    </UiCallout>
    <template v-else>
      <section
        v-if="page.sections.includes('workflows')"
        class="domain-section"
        aria-label="Workflows"
      >
        <UiSectionHeader
          title="Workflows"
          as="h2"
        />
        <div
          v-if="workflows.length"
          class="domain-workflows"
        >
          <div
            v-for="workflow in workflows"
            :key="`${workflow.source}:${workflow.key}`"
            class="domain-workflow"
          >
            <RouterLink
              class="focus-ring rounded-sm font-semibold text-fg-link hover:underline"
              :to="workflowLocation(projectId, slug, workflow.key)"
              :title="workflow.name"
            >
              {{ workflow.name }}
            </RouterLink>
            <p>{{ workflow.description }}</p>
          </div>
        </div>
        <p
          v-else-if="!error"
          class="text-sm text-fg-muted"
        >
          No workflows available for this domain.
        </p>
      </section>
      <DomainContentSection
        v-for="section in contentSections"
        ref="contentReaders"
        :key="`${projectId}:${slug}:${section}`"
        :project-id="projectId"
        :plugin-slug="slug"
        :kind="section"
      />
      <p
        v-if="!page.sections.length"
        class="text-sm text-fg-muted"
      >
        This plugin does not declare a domain page.
      </p>
    </template>
  </UiPageShell>
</template>

<style scoped>
.domain-page :deep(.ui-page-header h1) { white-space: normal; overflow-wrap: anywhere; }
.domain-section { min-width: 0; display: grid; gap: 16px; padding: 20px 0 16px; }
.domain-section :deep(.ui-section-header) { margin-bottom: 0; }
.domain-workflows { border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); }
.domain-workflow { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: 20px; align-items: center; padding: 14px 16px; font-size: var(--fs-sm); overflow-wrap: anywhere; }
.domain-workflow + .domain-workflow { border-top: 1px solid var(--color-border-subtle); }
.domain-workflow p { color: var(--color-fg-muted); }
@media (max-width: 900px) { .domain-workflow { grid-template-columns: minmax(0, 1fr); gap: 8px; } }
</style>
