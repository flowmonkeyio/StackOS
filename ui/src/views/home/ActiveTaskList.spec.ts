import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { callOperation } from '@/lib/operations'
import ActiveTaskList from './ActiveTaskList.vue'
import { activeTasksFixture } from './activeTaskFixtures'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/task-tracker/liveEvents', () => ({ openTrackerStatusStream: vi.fn(() => ({ close: vi.fn() })) }))
afterEach(() => { vi.mocked(callOperation).mockReset() })

describe('active-list read states', () => {
  it('distinguishes loading, failed reads and measured empty membership', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementation(() => new Promise(resolve => { finish = resolve }) as never)
    const wrapper = mount(ActiveTaskList, { global: { plugins: [createPinia()] } })
    try {
      await flushPromises()
      expect(wrapper.find('[aria-label="Loading active tasks"]').exists()).toBe(true)
      expect(wrapper.text()).not.toContain('0 tasks')
      expect(wrapper.text()).not.toContain('No tasks are in progress')
      finish(activeTasksFixture(null, []))
      await flushPromises()
      expect(wrapper.text()).toContain('No tasks are in progress.')
      expect(wrapper.text()).toContain('0 tasks')
    } finally { wrapper.unmount() }
    vi.mocked(callOperation).mockRejectedValue(new Error('Fixture service unavailable'))
    const failed = mount(ActiveTaskList, { global: { plugins: [createPinia()] } })
    try {
      await flushPromises()
      expect(failed.text()).toContain('Fixture service unavailable')
      expect(failed.text()).not.toContain('No tasks are in progress')
      expect(failed.text()).not.toContain('0 tasks')
    } finally { failed.unmount() }
  })
})
