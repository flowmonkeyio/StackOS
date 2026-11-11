import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ProjectRenameDialog from './ProjectRenameDialog.vue'
import { useProjectsStore, type Project } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'

enableAutoUnmount(afterEach)
const originalFetch = globalThis.fetch
const project: Project = {
  id: 2, name: 'Clickgram', slug: 'clickgram', order_index: 2,
  domain: 'clickgram.local', niche: null, locale: 'en-US', is_active: true,
  schedule_json: null, created_at: '', updated_at: '',
}
beforeEach(() => setActivePinia(createPinia()))
afterEach(() => {
  globalThis.fetch = originalFetch
  useToastsStore().clear()
  vi.restoreAllMocks()
})

function setup() {
  const projects = useProjectsStore()
  projects.items = [project]
  projects.activeProjectId = 2
  const wrapper = mount(ProjectRenameDialog, {
    attachTo: document.body, props: { project }, global: { stubs: { teleport: true } },
  })
  return { wrapper, projects, save: () => wrapper.get('button[type="submit"]') }
}

describe('ProjectRenameDialog', () => {
  it('selects the current name, disables unchanged/invalid saves, and cancels without a mutation', async () => {
    globalThis.fetch = vi.fn()
    const { wrapper, save } = setup()
    await flushPromises()
    const input = wrapper.get('input')
    expect(document.activeElement).toBe(input.element)
    expect((input.element as HTMLInputElement).selectionStart).toBe(0)
    expect((input.element as HTMLInputElement).selectionEnd).toBe(9)
    expect(save().attributes('disabled')).toBeDefined()
    await input.setValue('  Clickgram  ')
    expect(save().attributes('disabled')).toBeDefined()
    await input.setValue(' ')
    expect(wrapper.text()).toContain('Enter a project name.')
    expect(input.attributes('aria-invalid')).toBe('true')
    expect(save().attributes('disabled')).toBeDefined()
    await input.setValue('x'.repeat(201))
    expect(wrapper.text()).toContain('Use 200 characters or fewer.')
    await input.setValue('Clickgram Studio')
    expect(save().attributes('disabled')).toBeUndefined()
    await wrapper.findAll('button').find((button) => button.text() === 'Cancel')!.trigger('click')
    expect(wrapper.emitted('close')).toEqual([[]])
    expect(fetch).not.toHaveBeenCalled()
  })

  it('protects saving from close/duplicate submit, retains a failed draft and retries successfully', async () => {
    let rejectWrite!: (error: Error) => void
    let resolveWrite!: (response: Response) => void
    let writes = 0
    globalThis.fetch = vi.fn((input) => {
      if (!String(input).includes('/operations/')) return Promise.resolve(json({ items: [project], next_cursor: null, total_estimate: 1 }))
      writes++
      if (writes === 1) return new Promise<Response>((_resolve, reject) => { rejectWrite = reject })
      return new Promise<Response>((resolve) => { resolveWrite = resolve })
    })
    const { wrapper, projects, save } = setup()
    await wrapper.get('input').setValue('Clickgram Studio')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.text()).toContain('Saving…')
    expect(wrapper.get('input').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('button').find((button) => button.text() === 'Cancel')!.attributes('disabled')).toBeDefined()
    expect(wrapper.find('[aria-label="Close dialog"]').exists()).toBe(false)
    await wrapper.get('form').trigger('submit')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(wrapper.emitted('close')).toBeUndefined()
    expect(writes).toBe(1)
    rejectWrite(new TypeError('offline'))
    await flushPromises()
    expect(wrapper.get('input').element.value).toBe('Clickgram Studio')
    expect(wrapper.text()).toContain("Couldn't save the name. Try again.")
    expect(save().attributes('disabled')).toBeUndefined()
    expect(projects.managementPending).toBe(false)
    await wrapper.get('form').trigger('submit')
    resolveWrite(json({ data: { ...project, name: 'Clickgram Studio' } }))
    await flushPromises()
    expect(projects.activeProject?.name).toBe('Clickgram Studio')
    expect(projects.activeProject?.slug).toBe('clickgram')
    expect(useToastsStore().items[0].title).toBe('Project renamed')
    expect(wrapper.emitted('close')).toEqual([[]])
  })
})

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } })
}
