import { afterEach, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from './App.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import ProjectRenameDialog from '@/components/ProjectRenameDialog.vue'
import { useProjectsStore, type Project } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'

enableAutoUnmount(afterEach)
const originalFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = originalFetch
  useToastsStore().clear()
  vi.restoreAllMocks()
})

it('routes menu intents through the real shell, shared store and canonical operations without changing selection', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const projects = useProjectsStore()
  const rows: Project[] = [1, 2].map((id) => ({
    id, order_index: id, name: id === 1 ? 'Alpha' : 'Beta', slug: `project-${id}`,
    domain: 'example.test', niche: null, locale: 'en-US', is_active: true,
    schedule_json: null, created_at: '', updated_at: '',
  }))
  projects.items = rows
  projects.activeProjectId = 1
  const requests: { url: string; args: Record<string, unknown> }[] = []
  let ordered = rows
  globalThis.fetch = vi.fn(async (input, init) => {
    const url = String(input)
    if (url.startsWith('/api/v1/projects?')) return json({ items: ordered, total_estimate: 2, next_cursor: null })
    if (url.startsWith('/api/v1/operations/')) {
      const args = JSON.parse(String(init?.body)).arguments
      requests.push({ url, args })
      if (url.includes('project.update')) {
        const target = ordered.find((project) => project.id === args.project_id)!
        ordered = ordered.map((project) => project.id === target.id ? { ...project, name: args.patch.name } : project)
        return json({ data: { ...target, name: args.patch.name } })
      }
      if (url.includes('project.move')) {
        ordered = [{ ...ordered[1], order_index: 0 }, ordered[0]]
        return json({ data: ordered[0] })
      }
    }
    return json([])
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div />' } },
    { path: '/projects/:id/connections', component: { template: '<div />' } },
  ] })
  await router.push('/projects/1/connections')
  const app = mount(App, { attachTo: document.body, global: { plugins: [pinia, router], stubs: { DesktopUpdatePrompt: true } } })
  await flushPromises()
  const switcher = app.getComponent(ProjectSwitcher)
  await switcher.get('button').trigger('click')
  await flushPromises()
  await switcher.get('button[aria-label="Actions for Alpha"]').trigger('click')
  await switcher.get('[role="menu"]').findAll('button')[0].trigger('click')
  const dialog = new DOMWrapper(document.querySelector<HTMLElement>('.ui-dialog__overlay')!)
  await dialog.get('input').trigger('mousedown')
  expect(switcher.find('[aria-label="Projects"]').exists()).toBe(true)
  await dialog.get('input').setValue('Alpha Studio')
  await dialog.get('form').trigger('submit')
  await flushPromises()
  expect(app.findComponent(ProjectRenameDialog).exists()).toBe(false)
  expect(switcher.get('button').text()).toContain('Alpha Studio')
  expect(document.activeElement).toBe(switcher.get('button[aria-label="Actions for Alpha Studio"]').element)
  expect(projects.activeProject?.name).toBe('Alpha Studio')
  expect(router.currentRoute.value.fullPath).toBe('/projects/1/connections')
  await switcher.get('button[aria-label="Actions for Beta"]').trigger('click')
  await switcher.get('[role="menu"]').findAll('button')[1].trigger('click')
  await flushPromises()
  expect(projects.items.map((project) => project.id)).toEqual([2, 1])
  expect(projects.activeProjectId).toBe(1)
  expect(requests).toEqual([
    { url: '/api/v1/operations/project.update/call', args: { response_mode: 'raw', project_id: 1, scope: 'catalog', patch: { name: 'Alpha Studio' } } },
    { url: '/api/v1/operations/project.move/call', args: { response_mode: 'raw', project_id: 2, scope: 'catalog', placement: 'before', anchor_id: 1 } },
  ])
  // The next ticket owns confirmation/cleanup. This entry must emit only an exact target intent.
  await switcher.get('button[aria-label="Actions for Beta"]').trigger('click')
  await switcher.get('[role="menu"]').findAll('button').at(-1)!.trigger('click')
  expect(switcher.emitted('delete')).toEqual([[2]])
  expect(requests).toHaveLength(2)
  expect(router.currentRoute.value.fullPath).toBe('/projects/1/connections')
})

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } })
}
