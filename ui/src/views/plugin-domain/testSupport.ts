import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { Component } from 'vue'
import { PluginSource, type SchemaLoadedWorkflowTemplate, type SchemaPluginOut } from '@/api'

export const page = (items: object[], next_cursor: number | null = null) => ({ items, next_cursor, total_estimate: items.length })
export function plugin(slug = 'engineering', sections = ['workflows', 'saved', 'files'], enabled = true) {
  return { id: 1, slug, name: slug === 'engineering' ? 'Engineering' : slug, description: 'Domain description', enabled_for_project: enabled, manifest_json: { ui: { page: { sections } } }, source: PluginSource.builtin, version: '1.0.0', created_at: '2026-11-09', updated_at: '2026-11-09' } satisfies SchemaPluginOut
}
export function workflow(key = 'engineering.tracked-delivery', slug = 'engineering'): SchemaLoadedWorkflowTemplate {
  return {
    summary: { key, name: 'Tracked delivery', source: 'plugin', plugin_slug: slug, version: '1.0.0' },
    spec: { key, name: 'Tracked delivery', version: '1.0.0', description: 'Plan, build, review and verify changes.',
      steps: [{ id: 'prepare', title: 'Prepare' }, { id: 'check', title: 'Check', depends_on: ['prepare'], approval_refs: ['approve'] }],
      inputs: [{ key: 'goal', name: 'Goal', required: true }, { key: 'context', name: 'Context', required: false }],
      outputs: [{ key: 'report', name: 'Report' }], approval_gates: [{ key: 'approve', description: 'Confirm before release' }],
    },
  } as SchemaLoadedWorkflowTemplate
}
export async function openView(component: Component, path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/projects/:id/plugins/:pluginSlug', component },
    { path: '/projects/:id/plugins/:pluginSlug/workflows/:templateKey', component },
    { path: '/projects/:id/workflows/:templateKey', component },
    { path: '/projects/:id', component: { template: '<div>Project Home</div>' } },
    { path: '/projects/:id/library', component: { template: '<div>Library destination</div>' } },
  ] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [router, createPinia()], stubs: {
    ProjectPageHeader: { props: ['title', 'description'], template: '<header><h1>{{ title }}</h1><p>{{ description }}</p><slot name="actions"/></header>' },
  } } })
  return { wrapper, router }
}
