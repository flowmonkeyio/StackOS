import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import DesktopUpdatePrompt from './DesktopUpdatePrompt.vue'

describe('DesktopUpdatePrompt', () => {
  afterEach(() => {
    Reflect.deleteProperty(window, 'stackosDesktop')
    vi.restoreAllMocks()
  })

  it('does not render or call desktop update APIs in a browser session', async () => {
    const wrapper = mount(DesktopUpdatePrompt)
    await flushPromises()

    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('surfaces an available update and advances download/install from the prompt', async () => {
    const checkForUpdates = vi.fn(async () => ({
      ok: true,
      state: {
        enabled: true,
        status: 'available',
        updateInfo: { version: '1.0.1' },
      },
    }))
    const downloadUpdate = vi.fn(async () => ({
      ok: true,
      state: {
        enabled: true,
        status: 'downloaded',
        updateInfo: { version: '1.0.1' },
      },
    }))
    const installUpdate = vi.fn(async () => ({
      ok: true,
      state: {
        enabled: true,
        status: 'downloaded',
        updateInfo: { version: '1.0.1' },
      },
    }))

    Object.defineProperty(window, 'stackosDesktop', {
      configurable: true,
      value: {
        status: vi.fn(async () => ({})),
        updateState: vi.fn(async () => ({ enabled: true, status: 'idle' })),
        checkForUpdates,
        downloadUpdate,
        installUpdate,
      },
    })

    const wrapper = mount(DesktopUpdatePrompt)

    await vi.waitFor(() => expect(wrapper.text()).toContain('StackOS 1.0.1 is available'))
    await wrapper.get('button').trigger('click')
    await vi.waitFor(() => expect(downloadUpdate).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(wrapper.text()).toContain('Update ready to install'))

    await wrapper.get('button').trigger('click')
    await vi.waitFor(() => expect(installUpdate).toHaveBeenCalledTimes(1))
  })

  it('installs from the official downloaded update state', async () => {
    const installUpdate = vi.fn(async () => ({ ok: true }))

    Object.defineProperty(window, 'stackosDesktop', {
      configurable: true,
      value: {
        status: vi.fn(async () => ({})),
        updateState: vi.fn(async () => ({
          enabled: true,
          status: 'downloaded',
          updateInfo: { version: '1.0.1' },
        })),
        checkForUpdates: vi.fn(async () => ({ ok: true })),
        downloadUpdate: vi.fn(async () => ({ ok: true })),
        installUpdate,
      },
    })

    const wrapper = mount(DesktopUpdatePrompt)

    await vi.waitFor(() => expect(wrapper.text()).toContain('Update ready to install'))
    await wrapper.get('button').trigger('click')
    await vi.waitFor(() => expect(installUpdate).toHaveBeenCalledTimes(1))
  })
})
