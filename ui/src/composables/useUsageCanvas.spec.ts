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
})
