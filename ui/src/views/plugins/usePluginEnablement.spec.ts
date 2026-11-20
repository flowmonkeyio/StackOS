import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { usePluginEnablement } from './usePluginEnablement'

function deferred() {
  let resolve!: () => void
  let reject!: (error: Error) => void
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function setup() {
  const projectId = ref(1)
  const request = deferred()
  const actions = { enable: vi.fn(() => request.promise), refresh: vi.fn(async () => undefined) }
  let state!: ReturnType<typeof usePluginEnablement>
  const wrapper = mount(defineComponent({
    setup() { state = usePluginEnablement(projectId, actions); return () => null },
  }))
  return { projectId, request, actions, state, wrapper }
}

describe('usePluginEnablement', () => {
  it('submits once during repeated clicks, then refreshes after confirmation', async () => {
    const { state, actions, request, wrapper } = setup()
    const running = state.enable('finance', 'Finance')
    await state.enable('finance', 'Finance')
    expect(actions.enable).toHaveBeenCalledTimes(1)
    expect(actions.enable).toHaveBeenCalledWith(1, 'finance')
    expect(state.pendingSlug.value).toBe('finance')
    expect(actions.refresh).not.toHaveBeenCalled()
    request.resolve()
    await running
    expect(actions.refresh).toHaveBeenCalledTimes(1)
    expect(actions.refresh).toHaveBeenCalledWith(1)
    expect(state.message.value).toBe('Finance is enabled for this project.')
    expect(state.pendingSlug.value).toBeNull()
    wrapper.unmount()
  })

  it('surfaces failure without claiming enabled or refreshing', async () => {
    const { state, actions, request, wrapper } = setup()
    const running = state.enable('finance', 'Finance')
    request.reject(new Error('Permission denied'))
    await running
    expect(state.error.value).toContain('Permission denied')
    expect(state.message.value).toBeNull()
    expect(state.pendingSlug.value).toBeNull()
    expect(actions.refresh).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it.each(['resolve', 'reject'] as const)('ignores a stale %s after project navigation', async (outcome) => {
    const { state, actions, request, projectId, wrapper } = setup()
    const running = state.enable('finance', 'Finance')
    projectId.value = 2
    // App keys route views by project scope; the previous scope is disposed.
    wrapper.unmount()
    if (outcome === 'resolve') request.resolve()
    else request.reject(new Error('Old project failure'))
    await running
    expect(actions.enable).toHaveBeenCalledTimes(1)
    expect(actions.enable).toHaveBeenCalledWith(1, 'finance')
    expect(actions.refresh).not.toHaveBeenCalled()
    expect(state.message.value).toBeNull()
    expect(state.error.value).toBeNull()
    expect(state.pendingSlug.value).toBeNull()
  })

  it('does not refresh another page after unmount', async () => {
    const { state, actions, request, wrapper } = setup()
    const running = state.enable('finance', 'Finance')
    wrapper.unmount()
    request.resolve()
    await running
    expect(actions.refresh).not.toHaveBeenCalled()
  })
})
