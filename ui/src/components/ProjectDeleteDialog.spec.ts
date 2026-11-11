import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ProjectDeleteDialog from './ProjectDeleteDialog.vue'
import { useProjectsStore, type Project } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'

enableAutoUnmount(afterEach)
const originalFetch = globalThis.fetch
const project: Project = {
  id: 2, name: 'Clickgram', slug: 'immutable-clickgram', order_index: 2,
  domain: 'example.test', niche: null, locale: 'en-US', is_active: true,
  schedule_json: null, created_at: '', updated_at: '',
}
beforeEach(() => setActivePinia(createPinia()))
afterEach(() => {
  globalThis.fetch = originalFetch
  useToastsStore().clear()
  vi.restoreAllMocks()
})
function setup(row = project) {
  const projects = useProjectsStore()
  projects.items = [row]
  const finish = vi.fn(async () => true)
  const viewWork = vi.fn(async () => true)
  const wrapper = mount(ProjectDeleteDialog, {
    attachTo: document.body, props: { project: row, finish, viewWork }, global: { stubs: { teleport: true } },
  })
  const button = (label: string) => wrapper.findAll('button').find((item) => item.text() === label)!
  return { wrapper, projects, finish, viewWork, button }
}

describe('ProjectDeleteDialog', () => {
  it.each(['Cancel', 'escape', 'backdrop', 'close'])('shows approved identity/copy and %s cancels without side effects', async (action) => {
    globalThis.fetch = vi.fn()
    const { wrapper, button } = setup()
    await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('Delete Clickgram?')
    for (const copy of ['immutable-clickgram', "Permanently delete this project's StackOS records and managed files.",
      'Includes tasks, runs, history, automations and saved information.', 'Repository folders on your computer will remain.', 'This cannot be undone.']) {
      expect(wrapper.text()).toContain(copy)
    }
    expect(wrapper.find('input').exists()).toBe(false)
    if (action === 'Cancel') await button('Cancel').trigger('click')
    if (action === 'escape') window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    if (action === 'backdrop') await wrapper.get('.ui-dialog__overlay').trigger('click')
    if (action === 'close') await wrapper.get('[aria-label="Close dialog"]').trigger('click')
    expect(wrapper.emitted('close')).toEqual([[]])
    expect(fetch).not.toHaveBeenCalled()
  })

  it('protects in-flight cleanup, shows incomplete truth and retries the same target', async () => {
    let complete!: (response: Response) => void
    globalThis.fetch = vi.fn(() => new Promise<Response>((resolve) => { complete = resolve }))
    const { wrapper, projects, finish, button } = setup()
    ;(button('Delete project').element as HTMLButtonElement).focus()
    await button('Delete project').trigger('click')
    expect(wrapper.get('[role="dialog"]').element.contains(document.activeElement)).toBe(true)
    expect(wrapper.findAll('button:not([disabled])')).toHaveLength(0)
    for (const shiftKey of [false, true]) {
      const tab = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true })
      window.dispatchEvent(tab)
      expect(tab.defaultPrevented).toBe(true)
      expect(wrapper.get('[role="dialog"]').element.contains(document.activeElement)).toBe(true)
    }
    expect(wrapper.text()).toContain('Deleting Clickgram…')
    expect(button('Cancel').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[aria-label="Close dialog"]').exists()).toBe(false)
    await button('Deleting…').trigger('click')
    await wrapper.get('.ui-dialog__overlay').trigger('click')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(wrapper.emitted('close')).toBeUndefined()
    expect(fetch).toHaveBeenCalledTimes(1)
    complete(json({ data: { project_id: 2, status: 'incomplete', operation_id: 'retained', retryable: true, blockers: [] } }))
    await flushPromises()
    expect(wrapper.text()).toContain("Couldn't finish deleting Clickgram")
    expect(wrapper.text()).toContain('Some project data may already be removed. Retry to finish cleanup.')
    expect(projects.getById(2)?.cleanup_json?.operation_id).toBe('retained')
    expect(finish).not.toHaveBeenCalled()
    await button('Retry cleanup').trigger('click')
    complete(json({ data: { project_id: 2, status: 'deleted', blockers: [] } }))
    await flushPromises()
    expect(vi.mocked(fetch).mock.calls.map(([, init]) => JSON.parse(String(init?.body)).arguments.confirmation)).toEqual([
      { permanent: true, project_id: 2, project_slug: 'immutable-clickgram' },
      { permanent: true, project_id: 2, project_slug: 'immutable-clickgram' },
    ])
    expect(finish).toHaveBeenCalledWith(2)
    expect(wrapper.emitted('close')).toEqual([[]])
    expect(useToastsStore().items[0].title).toBe('Clickgram deleted')
  })

  it('routes blocked work to existing controls without invoking stop or another deletion', async () => {
    globalThis.fetch = vi.fn(async () => json({ data: { project_id: 2, status: 'blocked', blockers: ['running_work'] } }))
    const { wrapper, button, viewWork, finish } = setup()
    await button('Delete project').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Stop running work before deleting this project.')
    expect(finish).not.toHaveBeenCalled()
    await button('View active work').trigger('click')
    await flushPromises()
    expect(viewWork).toHaveBeenCalledWith(2)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('close')).toEqual([[]])
  })

  it('reports shared-reference blockers and administrator ownership gaps without inventing stop/ownership authority', async () => {
    globalThis.fetch = vi.fn(async () => json({ data: { project_id: 2, status: 'blocked', blockers: ['shared_artifact_pin'] } }))
    const { wrapper, button } = setup()
    await button('Delete project').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Another project still uses')
    expect(wrapper.text()).not.toContain('View active work')
    globalThis.fetch = vi.fn(async () => json({ data: {
      project_id: 2, status: 'incomplete', blockers: [], ownership_required_artifact_ids: [4],
    } }))
    await button('Retry deletion').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('administrator to confirm ownership')
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain('project.delete')
  })

  it('handles uncertain failure and reopens pending cleanup with same-target retry', async () => {
    globalThis.fetch = vi.fn(async () => { throw new TypeError('Connection unavailable') })
    const { wrapper, button, projects } = setup({ ...project, is_active: false, cleanup_json: { status: 'incomplete', operation_id: 'old' } })
    expect(wrapper.text()).toContain("Couldn't finish deleting")
    await button('Retry cleanup').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Connection unavailable')
    expect(wrapper.text()).toContain('Some project data may already be removed.')
    expect(button('Retry cleanup').attributes('disabled')).toBeUndefined()
    expect(projects.managementPending).toBe(false)
  })

  it('keeps a null deletion result uncertain and releases the dialog without an automatic retry', async () => {
    globalThis.fetch = vi.fn(async () => json(null))
    const { wrapper, button, projects, finish } = setup()
    await button('Delete project').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toBe('Could not confirm deletion. Retry cleanup to check this project.')
    expect(wrapper.text()).toContain('Some project data may already be removed.')
    expect(button('Retry cleanup').attributes('disabled')).toBeUndefined()
    expect(button('Close').attributes('disabled')).toBeUndefined()
    expect(projects.managementPending).toBe(false)
    expect(projects.getById(2)).not.toBeNull()
    expect(finish).not.toHaveBeenCalled()
    expect(useToastsStore().items).toHaveLength(0)
    expect(fetch).toHaveBeenCalledTimes(1)
    await button('Close').trigger('click')
    expect(wrapper.emitted('close')).toEqual([[]])
  })

  it('keeps missing identity and retries failed navigation without redispatching deletion', async () => {
    globalThis.fetch = vi.fn(async () => json({ data: { project_id: 2, status: 'missing', blockers: [] } }))
    const { wrapper, button, finish } = setup()
    finish.mockResolvedValueOnce(false)
    await button('Delete project').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Could not open the remaining project or Overview')
    expect(useToastsStore().items).toHaveLength(0)
    await button('Retry navigation').trigger('click')
    await flushPromises()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(finish).toHaveBeenCalledTimes(2)
    expect(useToastsStore().items[0].title).toBe('Clickgram is no longer available')
    expect(wrapper.emitted('close')).toEqual([[]])
  })

  it('uses the current name at confirmation and retains it after the store removes the renamed target', async () => {
    globalThis.fetch = vi.fn(async () => json({ data: { project_id: 2, status: 'deleted', blockers: [] } }))
    const { wrapper, button, finish } = setup()
    finish.mockResolvedValue(false)
    await wrapper.setProps({ project: { ...project, name: 'Current canonical name' } })
    expect(wrapper.get('h2').text()).toBe('Delete Current canonical name?')
    await button('Delete project').trigger('click')
    await flushPromises()
    // Navigation retains the original target snapshot after list membership is gone.
    await wrapper.setProps({ project })
    expect(wrapper.get('h2').text()).toBe('Current canonical name deleted')
    expect(wrapper.text()).toContain('immutable-clickgram')
  })
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
}
