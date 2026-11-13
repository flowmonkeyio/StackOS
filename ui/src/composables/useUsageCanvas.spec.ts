import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import { usageFeedKey } from './useUsageFeed'
import * as usage from '@/lib/liveUsage'
import type { SchemaUsageSampleOut } from '@/api'

enableAutoUnmount(afterEach)
const sample = (second: number): SchemaUsageSampleOut => ({ at: `2026-11-08T01:00:0${second}Z`, input: 100 + second * 2,
  cache_read: 80, remaining_input: null, output: 5 + second, total: 105 + second * 3, cache_write: null, reasoning: null, source: null, intervals: null })

describe('shared visible usage preparation and paint', () => {
  let observe: (entries: { target: Element; isIntersecting: boolean }[]) => void
  let observed: Set<Element>
  let frames: Map<number, FrameRequestCallback>
  let hidden: boolean
  let draws: ReturnType<typeof vi.fn>
  let nextFrame: number
  beforeEach(() => {
    observed = new Set(); frames = new Map(); hidden = false; nextFrame = 0; draws = vi.fn()
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: typeof observe) { observe = callback }
      observe(target: Element) { observed.add(target) }
      unobserve(target: Element) { observed.delete(target) }
      disconnect() { observed.clear() }
    })
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame })
    vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id))
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(100)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(16)
    const context = { setTransform() {}, clearRect: draws, setLineDash() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fillRect() {} }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
  async function reveal(targets: Element[]) {
    observe(targets.map(target => ({ target, isIntersecting: true })))
    await nextTick()
    observe([...observed].filter(target => target.tagName === 'CANVAS').map(target => ({ target, isIntersecting: true })))
    await nextTick()
  }
  function flushFrame() { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)) }

  it('shares three-metric parsing, ignores same-minute heartbeats and hidden dirt, and releases pending paints', async () => {
    const prepare = vi.spyOn(usage, 'prepareUsageSamples'), bucket = vi.spyOn(usage, 'usageTraces')
    const feed = ref({ status: 'live' as const, receivedAt: Date.parse('2026-11-08T01:00:10Z') })
    const props = { compact: true, active: true, metrics: { ...agentStatsFixture().totals, samples: [sample(0), sample(2)] } }
    const wrappers = Array.from({ length: 6 }, () => mount(AgentUsageSummary, { props, global: { provide: { [usageFeedKey as symbol]: feed } } }))
    expect(prepare).not.toHaveBeenCalled()
    await reveal(wrappers.slice(0, 5).map(wrapper => wrapper.element))
    expect(prepare).toHaveBeenCalledTimes(5)
    expect(bucket).toHaveBeenCalledTimes(5)
    expect(wrappers.flatMap(wrapper => wrapper.findAll('canvas'))).toHaveLength(15)
    expect(frames.size).toBe(1)
    flushFrame()
    expect(draws).toHaveBeenCalledTimes(15)
    prepare.mockClear(); bucket.mockClear(); draws.mockClear()
    await wrappers[5]!.setProps({ metrics: { ...props.metrics, samples: [sample(0), sample(5)] } })
    feed.value = { status: 'live', receivedAt: Date.parse('2026-11-08T01:00:50Z') }
    await nextTick(); flushFrame()
    expect(prepare).not.toHaveBeenCalled(); expect(bucket).not.toHaveBeenCalled(); expect(draws).not.toHaveBeenCalled()
    await reveal([wrappers[5]!.element])
    flushFrame()
    expect(prepare).toHaveBeenCalledTimes(1)
    expect(draws).toHaveBeenCalledTimes(3)
    expect(wrappers[5]!.get('canvas').attributes('aria-label')).toContain('10 tokens')
    draws.mockClear()
    feed.value = { status: 'live', receivedAt: Date.parse('2026-11-08T01:01:00Z') }
    await nextTick()
    expect(frames.size).toBe(1)
    wrappers.forEach(wrapper => wrapper.unmount())
    expect(frames.size).toBe(0)
    flushFrame(); expect(draws).not.toHaveBeenCalled()
    expect(observed.size).toBe(0)
  })

  it('pauses hidden preparation/paint, then reveals current values without animating unchanged charts', async () => {
    const props = { compact: true, metrics: { ...agentStatsFixture().totals, samples: [sample(0), sample(2)] } }
    const wrapper = mount(AgentUsageSummary, { props })
    await reveal([wrapper.element]); flushFrame(); draws.mockClear()
    hidden = true; document.dispatchEvent(new Event('visibilitychange')); await nextTick()
    expect(wrapper.findAll('canvas')).toHaveLength(0)
    await wrapper.setProps({ metrics: { ...props.metrics, samples: [sample(0), sample(5)] } })
    document.documentElement.setAttribute('data-theme', 'dark')
    await nextTick(); flushFrame(); expect(draws).not.toHaveBeenCalled()
    hidden = false; document.dispatchEvent(new Event('visibilitychange')); await nextTick()
    await reveal([wrapper.element]); flushFrame()
    expect(draws).toHaveBeenCalledTimes(3)
    expect(wrapper.get('canvas').attributes('aria-label')).toContain('10 tokens')
    wrapper.unmount(); document.documentElement.removeAttribute('data-theme')
  })

  it('animates changed live geometry once, keeps historical/unchanged paints quiet, and cancels hidden/reduced/unmounted motion', async () => {
    let reduced = false
    let reduceChanged!: () => void
    const cancel = vi.fn()
    const animate = vi.fn(() => ({ cancel, onfinish: null }))
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
      get matches() { return query === '(prefers-reduced-motion: reduce)' && reduced },
      addEventListener: (_event: string, callback: () => void) => { if (query.startsWith('(prefers-reduced')) reduceChanged = callback },
      removeEventListener: vi.fn(),
    })))
    vi.stubGlobal('Animation', class {})
    Object.defineProperty(HTMLCanvasElement.prototype, 'animate', { configurable: true, value: animate })
    const samples = [sample(0), { ...sample(2), at: '2026-11-08T01:01:00Z' }, { ...sample(5), at: '2026-11-08T01:02:00Z' }]
    const feed = ref({ status: 'live' as 'live' | 'reconnecting', receivedAt: Date.parse('2026-11-08T01:02:10Z') })
    const metrics = { ...agentStatsFixture().totals, samples }
    const wrapper = mount(AgentUsageSummary, { props: { compact: true, active: true, metrics }, global: { provide: { [usageFeedKey as symbol]: feed } } })
    await reveal([wrapper.element]); flushFrame()
    expect(animate).not.toHaveBeenCalled()
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({ color: '', getPropertyValue: (key: string) => key === '--duration-base' ? '0.16s' : '' } as CSSStyleDeclaration)
    const update = async (input: number) => {
      await wrapper.setProps({ metrics: { ...metrics, samples: [...samples.slice(0, 2), { ...samples[2]!, input }] } })
      flushFrame()
    }
    await update(120)
    expect(animate).toHaveBeenCalledOnce()
    expect(animate.mock.calls[0]).toEqual([[{ opacity: 0.65 }, { opacity: 1 }], expect.objectContaining({ duration: 160 })])
    expect(frames.size).toBe(0) // no animation paint loop
    draws.mockClear(); animate.mockClear()
    await wrapper.setProps({ inline: true }); flushFrame()
    expect(animate).not.toHaveBeenCalled(); expect(draws).not.toHaveBeenCalled()
    reduced = true; reduceChanged()
    expect(cancel).toHaveBeenCalledOnce()
    await update(130); expect(animate).not.toHaveBeenCalled()
    reduced = false; reduceChanged()
    await update(140); expect(animate).toHaveBeenCalledOnce()
    hidden = true; document.dispatchEvent(new Event('visibilitychange')); await nextTick(); flushFrame()
    expect(cancel).toHaveBeenCalledTimes(2)
    draws.mockClear(); animate.mockClear()
    await update(150); expect(draws).not.toHaveBeenCalled(); expect(animate).not.toHaveBeenCalled()
    hidden = false; document.dispatchEvent(new Event('visibilitychange')); await nextTick()
    await reveal([wrapper.element]); flushFrame(); expect(animate).not.toHaveBeenCalled()
    await update(160); expect(animate).toHaveBeenCalledOnce()
    feed.value = { ...feed.value, status: 'reconnecting' }; await nextTick(); flushFrame()
    expect(cancel).toHaveBeenCalledTimes(3)
    animate.mockClear(); await update(170); expect(animate).not.toHaveBeenCalled()
    feed.value = { ...feed.value, status: 'live' }; await nextTick(); flushFrame()
    await wrapper.setProps({ active: false }); flushFrame()
    await update(180); expect(animate).not.toHaveBeenCalled()
    await wrapper.setProps({ active: true }); flushFrame()
    await update(190); expect(animate).toHaveBeenCalledOnce()
    wrapper.unmount(); expect(cancel).toHaveBeenCalledTimes(4)
    delete (HTMLCanvasElement.prototype as unknown as { animate?: unknown }).animate
    expect(frames.size).toBe(0)
  })
})
