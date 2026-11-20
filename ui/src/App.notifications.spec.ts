import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from './App.vue'
import { useAuthStore } from '@/stores/auth'
import { useProjectsStore } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'
import type { DesktopTaskNotice } from '@/lib/desktop'

let app: VueWrapper | undefined
let receive: (notice: DesktopTaskNotice) => void
const unsubscribe = vi.fn()
const openTask = vi.fn()
const notice: DesktopTaskNotice = {
  projectId: 1, taskId: 9, taskKey: 'work', revision: 1,
  title: 'Task complete', body: 'Publish the monthly report · Example project',
  tone: 'success', actionLabel: 'Open task', nativeStatus: 'system-controlled',
}

beforeEach(() => {
  vi.useFakeTimers()
  unsubscribe.mockClear()
  openTask.mockReset().mockResolvedValue({ ok: true })
  vi.stubGlobal('stackosDesktop', {
    status: vi.fn(), onTaskNotice: (listener: typeof receive) => { receive = listener; return unsubscribe },
    openNotificationTask: openTask,
  })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"items":[]}')))
})
afterEach(() => {
  app?.unmount()
  app = undefined
  useToastsStore().clear()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function shell() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().state = { kind: 'ready' }
  useAuthStore().token = 'fixture-only'
  useProjectsStore().catalogComplete = true
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<h1>Work</h1>' } }] })
  await router.push('/')
  app = mount(App, { global: { plugins: [pinia, router], stubs: { DesktopUpdatePrompt: true } } })
  await flushPromises()
  return app
}

it('presents selected outcomes through the real shell/store/UiToast and opens the exact target', async () => {
  const view = await shell()
  receive(notice)
  await flushPromises()
  expect(view.get('[role="status"]').text()).toContain(notice.body)
  await view.findAll('button').find((button) => button.text() === 'Open task')!.trigger('click')
  expect(openTask).toHaveBeenCalledWith({ projectId: 1, taskId: 9, taskKey: 'work' })
  // New state replaces the same task; the old completion timer cannot remove it.
  vi.advanceTimersByTime(4_000)
  receive({ ...notice, revision: 2, title: 'Task needs attention', tone: 'warning' })
  await flushPromises()
  vi.advanceTimersByTime(5_000)
  expect(view.findAll('.ui-toast')).toHaveLength(1)
  expect(view.get('.ui-toast').text()).toContain('Task needs attention')
  expect(view.find('.bg-warning').exists()).toBe(true)
  receive({ ...notice, revision: 3, title: 'Task failed', tone: 'danger' })
  await flushPromises()
  expect(view.get('[role="alert"]').attributes('aria-live')).toBe('assertive')
  expect(view.findAll('.ui-toast')).toHaveLength(1)
  await view.get('button[aria-label="Dismiss Task failed"]').trigger('click')
  expect(view.findAll('.ui-toast')).toHaveLength(0)
  view.unmount()
  app = undefined
  expect(unsubscribe).toHaveBeenCalledTimes(1)
})

it('retains in-app notices when native support is absent or fails, with a truthful action error', async () => {
  const view = await shell()
  receive({ ...notice, nativeStatus: 'unsupported' })
  await flushPromises()
  expect(view.text()).toContain('Native notifications are unavailable on this system.')
  receive({ ...notice, nativeStatus: 'failed' })
  openTask.mockResolvedValue({ ok: false })
  await flushPromises()
  expect(view.findAll('.ui-toast')).toHaveLength(1)
  expect(view.text()).toContain('Check system notification settings.')
  await view.findAll('button').find((button) => button.text() === 'Open task')!.trigger('click')
  await flushPromises()
  expect(view.text()).toContain('Task could not be opened')
})

it('does not subscribe or poll in a browser or older desktop shell', async () => {
  vi.stubGlobal('stackosDesktop', { status: vi.fn() })
  const view = await shell()
  expect(view.find('.ui-toast').exists()).toBe(false)
  vi.advanceTimersByTime(60_000)
  expect(view.find('.ui-toast').exists()).toBe(false)
})
