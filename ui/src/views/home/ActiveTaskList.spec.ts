import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { callOperation } from '@/lib/operations'
import { openTrackerStatusStream } from '@/lib/task-tracker/liveEvents'
import ActiveTaskList from './ActiveTaskList.vue'
import { activeTasksFixture } from './activeTaskFixtures'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/task-tracker/liveEvents', async original => ({ ...await original<object>(), openTrackerStatusStream: vi.fn(() => ({ close: vi.fn() })) }))
afterEach(() => { vi.mocked(callOperation).mockReset() })
function baseline() {
  const stream = vi.mocked(openTrackerStatusStream).mock.lastCall![0]
  stream.onFrame?.(Date.now())
  stream.onUsage?.({ tasks: [], tickets: [], replace: true })
}

describe('active-list read states', () => {
  it('distinguishes loading, failed reads and measured empty membership', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementation(() => new Promise(resolve => { finish = resolve }) as never)
    const wrapper = mount(ActiveTaskList, { global: { plugins: [createPinia()] } })
    try {
      expect(wrapper.find('[aria-label="Loading active tasks"]').exists()).toBe(true)
      expect(wrapper.text()).not.toContain('0 tasks')
      baseline()
      await flushPromises()
      expect(wrapper.find('[aria-label="Loading active tasks"]').exists()).toBe(true)
      expect(wrapper.text()).not.toContain('0 tasks')
      expect(wrapper.text()).not.toContain('No tasks are in progress')
      finish(activeTasksFixture(null, []))
      await flushPromises()
      expect(wrapper.text()).toContain('No tasks are in progress.')
      expect(wrapper.text()).toContain('0 tasks')
      let finishRefresh!: (value: unknown) => void
      vi.mocked(callOperation).mockImplementation(() => new Promise(resolve => { finishRefresh = resolve }) as never)
      const read = (wrapper.vm as unknown as { refresh: () => Promise<void> }).refresh()
      await flushPromises()
      expect(wrapper.find('[aria-label="Loading active tasks"]').exists()).toBe(false)
      expect(wrapper.text()).toContain('No tasks are in progress.')
      finishRefresh(activeTasksFixture(null, []))
      await read
    } finally { wrapper.unmount() }
    vi.mocked(callOperation).mockRejectedValue(new Error('Fixture service unavailable'))
    const failed = mount(ActiveTaskList, { global: { plugins: [createPinia()] } })
    try {
      baseline()
      await flushPromises()
      expect(failed.text()).toContain('Fixture service unavailable')
      expect(failed.text()).not.toContain('No tasks are in progress')
      expect(failed.text()).not.toContain('0 tasks')
    } finally { failed.unmount() }
  })
})
