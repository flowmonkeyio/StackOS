import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import type { SchemaDeliveryCapacityOut, SchemaProjectDeliveryCapacityOut } from '@/api'
import { callOperation } from '@/lib/operations'
import { useToastsStore } from '@/stores/toasts'
import DeliveryCapacityControl from './DeliveryCapacityControl.vue'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
const project = (id = 1, limit = 3, running = 4): SchemaProjectDeliveryCapacityOut => ({
  project_id: id, project_name: `Project ${id}`, project_slug: `project-${id}`,
  configured_limit: limit, running_count: running, queued_count: 2,
})
const state = (...projects: SchemaProjectDeliveryCapacityOut[]): SchemaDeliveryCapacityOut => ({
  scope: 'project', projects, supported_min: 1, supported_max: 32,
})
let wrapper: ReturnType<typeof mount>
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ''; vi.resetAllMocks() })

async function setup(projectId?: number) {
  vi.mocked(callOperation).mockResolvedValue(state(...(projectId ? [project(projectId)] : [project(), project(2, 2, 1)])))
  wrapper = mount(DeliveryCapacityControl, { props: { projectId }, attachTo: document.body, global: { plugins: [createPinia()] } })
  await flushPromises()
}
const open = async () => { await wrapper.get('[data-popover-trigger]').trigger('click'); await flushPromises() }
const save = async () => { await wrapper.get('form').trigger('submit'); await flushPromises() }
const input = (id = 1) => wrapper.get(`input[aria-label="Run at once for Project ${id}"]`)
const refresh = () => (wrapper.vm as unknown as { refresh: () => Promise<void> }).refresh()

describe('DeliveryCapacityControl', () => {
  it('renders all project queues and atomically saves changed rows using server readback', async () => {
    await setup(); await open()
    expect(wrapper.get('[data-popover-trigger]').text()).toBe('Task queues')
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    expect(wrapper.findAll('thead th').map(th => th.text())).toEqual(['Project', 'Running', 'Waiting', 'Run at once'])
    expect(wrapper.text()).toContain('Set how many tasks each project can run.')
    expect(wrapper.text()).toContain('Only Todo tasks enter the queue.')
    await input().setValue(6); await input(2).setValue(5)
    vi.mocked(callOperation).mockResolvedValueOnce({ data: state(project(1, 6), project(2, 5, 1)) })
    await save()
    expect(callOperation).toHaveBeenLastCalledWith('tracker.setDeliveryCapacity', { updates: [
      { project_id: 1, max_concurrent_tasks: 6 }, { project_id: 2, max_concurrent_tasks: 5 },
    ] })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('[data-popover-trigger]').element)
    expect(useToastsStore().items.at(-1)?.title).toBe('Task queues saved')
    vi.mocked(callOperation).mockResolvedValue(state(project(1, 6), project(2, 5, 1)))
    await open()
    expect((input().element as HTMLInputElement).value).toBe('6')
    expect((input(2).element as HTMLInputElement).value).toBe('5')
    await input().setValue(7); await save()
    expect(callOperation).toHaveBeenLastCalledWith('tracker.setDeliveryCapacity', { updates: [{ project_id: 1, max_concurrent_tasks: 7 }] })
  })

  it('scopes reads, rows and writes to one project including all-project save readback', async () => {
    await setup(2); await open()
    expect(callOperation).toHaveBeenCalledWith('tracker.deliveryCapacity', { project_id: 2 })
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('Project 1')
    await input(2).setValue(7)
    vi.mocked(callOperation).mockResolvedValueOnce({ data: state(project(), project(2, 7)) })
    await save()
    expect(callOperation).toHaveBeenLastCalledWith('tracker.setDeliveryCapacity', { updates: [{ project_id: 2, max_concurrent_tasks: 7 }] })
    vi.mocked(callOperation).mockResolvedValue(state(project(2, 7)))
    await open()
    expect((input(2).element as HTMLInputElement).value).toBe('7')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  })

  it('retains invalid and failed drafts and performs no no-op writes', async () => {
    await setup(); await open(); await save()
    expect(vi.mocked(callOperation).mock.calls.every(([name]) => name === 'tracker.deliveryCapacity')).toBe(true)
    for (const invalid of [33, 2.5, '']) {
      await input().setValue(invalid); await save()
      expect(wrapper.get('[role="alert"]').text()).toContain('whole number from 1 to 32')
    }
    await input().setValue(2)
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Storage unavailable'))
    await save()
    expect(wrapper.get('[role="alert"]').text()).toContain('Storage unavailable')
    expect((input().element as HTMLInputElement).value).toBe('2')
    expect(wrapper.get('tbody tr').text()).toContain('Saved: 3')
    expect(wrapper.get('[data-count="running"]').text()).toBe('4')
  })

  it('keeps rows and edited drafts through pending, successful and failed refreshes', async () => {
    await setup(); await open(); await input().setValue(8)
    let finish!: (value: SchemaDeliveryCapacityOut) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const pending = refresh(); await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    expect((input().element as HTMLInputElement).value).toBe('8')
    finish(state(project(1, 4, 6), project(2, 5, 2))); await pending; await flushPromises()
    expect((input().element as HTMLInputElement).value).toBe('8')
    expect((input(2).element as HTMLInputElement).value).toBe('5')
    expect(input().attributes('title')).toContain('Saved: 4')
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Counts unavailable'))
    await refresh(); await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    expect(wrapper.text()).toContain('Counts unavailable')
    expect(wrapper.get('[data-count="running"]').text()).toBe('—')
    expect(wrapper.get('[data-count="waiting"]').text()).toBe('—')
    expect((input().element as HTMLInputElement).value).toBe('8')
  })

  it('discards edits on cancel, Escape and outside dismissal without writes', async () => {
    await setup()
    for (const dismiss of ['cancel', 'escape', 'outside']) {
      await open(); await input().setValue(9)
      if (dismiss === 'cancel') await wrapper.findAll('button').find(b => b.text() === 'Cancel')!.trigger('click')
      else if (dismiss === 'escape') document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      else document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
      await flushPromises()
      expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
      expect(document.activeElement).toBe(wrapper.get('[data-popover-trigger]').element)
    }
    await open()
    expect((input().element as HTMLInputElement).value).toBe('3')
    expect(vi.mocked(callOperation).mock.calls.every(([name, args]) => name === 'tracker.deliveryCapacity' && Object.keys(args).length === 0)).toBe(true)
  })

  it('steps labeled limits and shows actual over-limit work without stopping tasks', async () => {
    await setup(); await open()
    for (const field of wrapper.findAll('input')) {
      expect(wrapper.get(`label[for="${field.attributes('id')}"]`).text()).toBe(field.attributes('aria-label'))
    }
    expect(wrapper.text()).toContain('Running tasks continue.')
    await wrapper.get('[aria-label="Decrease run at once for Project 1"]').trigger('click')
    expect((input().element as HTMLInputElement).value).toBe('2')
    await wrapper.get('[aria-label="Increase run at once for Project 1"]').trigger('click')
    expect((input().element as HTMLInputElement).value).toBe('3')
    expect(wrapper.get('[data-count="running"]').text()).toBe('4')
    expect(vi.mocked(callOperation).mock.calls.every(([name]) => name === 'tracker.deliveryCapacity')).toBe(true)
  })

  it('keeps the confirmed saved over-limit explanation after a failed raise', async () => {
    await setup(1)
    vi.mocked(callOperation).mockResolvedValue(state(project(1, 2)))
    await refresh(); await open()
    expect(wrapper.text()).toContain('Running tasks continue.')
    await input().setValue(7)
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Storage unavailable'))
    await save()
    expect(wrapper.get('[role="alert"]').text()).toContain('Storage unavailable')
    expect((input().element as HTMLInputElement).value).toBe('7')
    expect(input().attributes('title')).toContain('Saved: 2.')
    expect(wrapper.get('[data-count="running"]').text()).toBe('4')
    expect(wrapper.text()).toContain('Running tasks continue. New tasks wait until running work falls below the saved limit.')
  })

  it('uses confirmed saved capacity for an unsaved lower limit and its successful readback', async () => {
    await setup(1)
    vi.mocked(callOperation).mockResolvedValue(state(project(1, 7)))
    await refresh(); await open()
    expect(wrapper.text()).not.toContain('Running tasks continue.')
    await input().setValue(2)
    expect(input().attributes('title')).toContain('Saved: 7.')
    expect(wrapper.text()).not.toContain('Running tasks continue.')
    expect(vi.mocked(callOperation).mock.calls.every(([name]) => name === 'tracker.deliveryCapacity')).toBe(true)
    vi.mocked(callOperation).mockResolvedValueOnce({ data: state(project(1, 2)) })
    await save()
    vi.mocked(callOperation).mockResolvedValue(state(project(1, 2)))
    await open()
    expect((input().element as HTMLInputElement).value).toBe('2')
    expect(wrapper.get('[data-count="running"]').text()).toBe('4')
    expect(wrapper.text()).toContain('Running tasks continue.')
  })

  it('keeps saves single-flight and fences reads started before a save', async () => {
    await setup(); await open()
    let finishRead!: (value: SchemaDeliveryCapacityOut) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finishRead = resolve }))
    const pending = refresh(); await input().setValue(8)
    let finishSave!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve }))
    await wrapper.get('form').trigger('submit'); await wrapper.get('form').trigger('submit')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'tracker.setDeliveryCapacity')).toHaveLength(1)
    finishSave({ data: state(project(1, 8), project(2)) }); await flushPromises()
    finishRead(state(project(1, 3))); await pending; await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    vi.mocked(callOperation).mockResolvedValue(state(project(1, 8), project(2)))
    await open()
    expect((input().element as HTMLInputElement).value).toBe('8')
  })

  it.each(['read', 'save'])('fences a late %s and draft after scope changes', async pendingKind => {
    await setup(1); await open(); await input().setValue(9)
    let finish!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const pending = pendingKind === 'read' ? refresh() : wrapper.get('form').trigger('submit')
    vi.mocked(callOperation).mockResolvedValue(state(project(2, 5)))
    await wrapper.setProps({ projectId: 2 }); await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    finish(pendingKind === 'read' ? state(project(1, 9)) : { data: state(project(1, 9)) })
    await pending; await flushPromises(); await open()
    expect(wrapper.text()).not.toContain('Project 1')
    expect((input(2).element as HTMLInputElement).value).toBe('5')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  })

  it('shows unavailable reads and unknown counts without inventing zeroes', async () => {
    vi.mocked(callOperation).mockRejectedValue(new Error('Service unavailable'))
    wrapper = mount(DeliveryCapacityControl, { global: { plugins: [createPinia()] } })
    await flushPromises(); await open()
    expect(wrapper.text()).toContain('Service unavailable')
    expect(wrapper.find('input').exists()).toBe(false)
    vi.mocked(callOperation).mockResolvedValue(state({ ...project(), running_count: null, queued_count: null } as never))
    await refresh(); await flushPromises()
    expect(wrapper.get('[data-count="running"]').text()).toBe('—')
    expect(wrapper.get('[data-count="waiting"]').text()).toBe('—')
  })
})
