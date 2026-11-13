import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import { usageFeedKey } from '@/composables/useUsageFeed'
import type { UsageFeedState } from '@/lib/usageUpdates'
import UsageLiveIndicator from './UsageLiveIndicator.vue'

enableAutoUnmount(afterEach)
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('truthful live motion', () => {
  it('requires actual activity, fresh feed and visible demand, and stops at each boundary', async () => {
    let intersect!: (entries: { target: Element; isIntersecting: boolean }[]) => void
    const unobserve = vi.fn()
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: typeof intersect) { intersect = callback }
      observe() {}; unobserve = unobserve; disconnect() {}
    })
    let hidden = false
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
    const feed = ref<UsageFeedState>({ status: 'connecting', receivedAt: null })
    const wrapper = mount(UsageLiveIndicator, { props: { active: true }, global: { provide: { [usageFeedKey as symbol]: feed } } })
    intersect([{ target: wrapper.element, isIntersecting: true }]); await nextTick()
    expect(wrapper.text()).toBe('Connecting')
    expect(wrapper.find('.usage-live-indicator--motion').exists()).toBe(false)
    feed.value = { status: 'live', receivedAt: Date.now() }; await nextTick()
    expect(wrapper.get('.usage-live-indicator--motion').text()).toBe('Live')
    intersect([{ target: wrapper.element, isIntersecting: false }]); await nextTick()
    expect(wrapper.find('.usage-live-indicator--motion').exists()).toBe(false)
    intersect([{ target: wrapper.element, isIntersecting: true }]); await nextTick()
    hidden = true; document.dispatchEvent(new Event('visibilitychange')); await nextTick()
    expect(wrapper.find('.usage-live-indicator--motion').exists()).toBe(false)
    hidden = false; document.dispatchEvent(new Event('visibilitychange')); await nextTick()
    expect(wrapper.find('.usage-live-indicator--motion').exists()).toBe(true)
    feed.value = { status: 'reconnecting', receivedAt: feed.value.receivedAt }; await nextTick()
    expect(wrapper.text()).toBe('Reconnecting')
    expect(wrapper.find('.usage-live-indicator--motion').exists()).toBe(false)
    await wrapper.setProps({ active: false })
    feed.value = { status: 'live', receivedAt: Date.now() }; await nextTick()
    expect(wrapper.find('.usage-live-indicator').exists()).toBe(false)
    wrapper.unmount(); expect(unobserve).toHaveBeenCalledOnce()
  })
})
