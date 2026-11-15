import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UsageIntervalOutKind, type SchemaUsageSampleOut } from '@/api'
import UsageTrace from './UsageTrace.vue'

enableAutoUnmount(afterEach)
const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()))

const sample = (second: number): SchemaUsageSampleOut => ({
  at: `2026-11-08T01:00:0${second}Z`, input: 100 + second * 2, cache_read: 80,
  output: 5 + second, total: 105 + second * 3, cache_write: null, reasoning: null, remaining_input: null,
  source: null, intervals: null,
})
function drawing() {
  const context = { setTransform: vi.fn(), clearRect: vi.fn(), setLineDash: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), fillRect: vi.fn(), strokeStyle: '', fillStyle: '' }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(32)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(16)
  vi.stubGlobal('devicePixelRatio', 2)
  return context
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.documentElement.removeAttribute('data-theme') })

describe('reported activity mini-canvas', () => {
  it('keeps the same baseline and slot through empty, first reading and reported bars', async () => {
    const context = drawing()
    const wrapper = mount(UsageTrace, { props: { samples: [], metric: 'input' } })
    await frame()
    const canvas = wrapper.get('canvas').element as HTMLCanvasElement
    expect([canvas.width, canvas.height]).toEqual([64, 32])
    expect(context.setTransform).toHaveBeenLastCalledWith(2, 0, 0, 2, 0, 0)
    expect(context.setLineDash).toHaveBeenLastCalledWith([])
    expect(context.lineTo).toHaveBeenLastCalledWith(31, 14.5)
    expect(context.fillRect).not.toHaveBeenCalled()
    await wrapper.setProps({ samples: [sample(0)] })
    await frame()
    expect(context.lineTo).toHaveBeenLastCalledWith(31, 14.5)
    expect(context.fillRect).not.toHaveBeenCalled()
    expect(wrapper.attributes('aria-label')).toContain('First reading establishes a baseline')
    context.fillRect.mockClear()
    await wrapper.setProps({ samples: [sample(0), sample(2)] })
    await frame()
    expect(context.lineTo).toHaveBeenLastCalledWith(31, 14.5)
    expect(context.fillRect).toHaveBeenCalledOnce()
    const [firstX, firstY, firstWidth, firstHeight] = context.fillRect.mock.lastCall!
    expect(firstX).toBeGreaterThan(29.5)
    expect(firstY).toBe(2.5)
    expect(firstWidth).toBeLessThanOrEqual(2)
    expect(firstHeight).toBe(12)
    expect(wrapper.attributes('role')).toBe('img')
    expect(wrapper.attributes('aria-label')).toContain('4 tokens')
    expect(wrapper.attributes('title')).toBe(wrapper.attributes('aria-label'))
    const before = context.clearRect.mock.calls.length
    await wrapper.setProps({ samples: [sample(0), sample(2), sample(5)] })
    await frame()
    expect(context.clearRect.mock.calls.length).toBeGreaterThan(before)
    expect(wrapper.get('canvas').element).toBe(canvas)
    expect(wrapper.find('svg').exists()).toBe(false)
    wrapper.unmount()
  })

  it('moves an active reporting window only when its shared clock changes', async () => {
    const context = drawing()
    const samples = [sample(0), sample(2)]
    const wrapper = mount(UsageTrace, { props: { samples, metric: 'input' } })
    await frame()
    const canvas = wrapper.element
    const lastBar = context.fillRect.mock.lastCall!
    context.fillRect.mockClear()
    await wrapper.setProps({ endAt: Date.parse(samples[0]!.at) + 60_000 })
    await frame()
    expect(context.fillRect.mock.lastCall![0]).toBeCloseTo(lastBar[0] - 1.5)
    expect(wrapper.element).toBe(canvas)
    await wrapper.setProps({ endAt: undefined })
    await frame()
    expect(context.fillRect.mock.lastCall).toEqual(lastBar)
    wrapper.unmount()
  })

  it('keeps bars slim when a wider canvas or a custom count provides more space', async () => {
    const context = drawing()
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(120)
    const wrapper = mount(UsageTrace, { props: { samples: [sample(0), sample(2)], metric: 'input', barCount: 5 } })
    await frame()
    expect(context.fillRect.mock.lastCall![2]).toBe(2)
    expect(wrapper.attributes('title')).toContain('5 one-minute intervals')
    await frame()
    const canvas = wrapper.element
    await wrapper.setProps({ barCount: 20 })
    await frame()
    expect(context.fillRect.mock.lastCall![2]).toBe(2)
    expect(wrapper.attributes('title')).toContain('20 one-minute intervals')
    expect(wrapper.element).toBe(canvas)
    wrapper.unmount()
  })

  it('redraws for size, theme and DPR, then releases all observers and listeners', async () => {
    const context = drawing()
    let resize!: () => void
    const disconnect = vi.fn()
    vi.stubGlobal('ResizeObserver', class { constructor(callback: (entries: { target: Element }[]) => void) { resize = () => callback([{ target: document.querySelector('canvas')! }]) }; observe() {}; unobserve() {}; disconnect = disconnect })
    const queries: { addEventListener: ReturnType<typeof vi.fn>; removeEventListener: ReturnType<typeof vi.fn> }[] = []
    vi.stubGlobal('matchMedia', vi.fn(() => {
      const query = { addEventListener: vi.fn(), removeEventListener: vi.fn() }
      queries.push(query)
      return query
    }))
    const add = vi.spyOn(window, 'addEventListener'), remove = vi.spyOn(window, 'removeEventListener')
    const mutationDisconnect = vi.spyOn(MutationObserver.prototype, 'disconnect')
    const style = (color: string) => ({ color, getPropertyValue: () => 'rgb(100, 100, 100)' } as unknown as CSSStyleDeclaration)
    const color = vi.spyOn(window, 'getComputedStyle').mockReturnValue(style('rgb(1, 2, 3)'))
    const wrapper = mount(UsageTrace, { attachTo: document.body, props: { samples: [sample(0), sample(2)], metric: 'output' } })
    await frame()
    color.mockReturnValue(style('rgb(4, 5, 6)'))
    document.documentElement.setAttribute('data-theme', 'dark')
    await flushPromises(); await frame()
    expect(context.fillStyle).toBe('rgb(4, 5, 6)')
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(70)
    resize(); await frame()
    expect((wrapper.element as HTMLCanvasElement).width).toBe(140)
    vi.stubGlobal('devicePixelRatio', 3)
    const changed = queries[0]!.addEventListener.mock.calls[0]![1] as () => void
    changed(); await frame()
    expect((wrapper.element as HTMLCanvasElement).width).toBe(210)
    expect(queries[0]!.removeEventListener).toHaveBeenCalledWith('change', changed)
    expect(queries).toHaveLength(3) // DPR is rearmed; reduced motion has one shared query.
    const draw = add.mock.calls.find(call => call[0] === 'resize')![1]
    wrapper.unmount()
    expect(disconnect).toHaveBeenCalledOnce()
    expect(mutationDisconnect).toHaveBeenCalledOnce()
    expect(remove).toHaveBeenCalledWith('resize', draw)
    expect(queries[2]!.removeEventListener).toHaveBeenCalledWith('change', changed)
    expect(queries[1]!.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function))
    const count = context.clearRect.mock.calls.length
    window.dispatchEvent(new Event('resize'))
    document.documentElement.setAttribute('data-theme', 'light')
    await flushPromises()
    expect(context.clearRect).toHaveBeenCalledTimes(count)
  })

  it.each(['input', 'cache_read', 'output'] as const)('draws a sparse %s interval in one color without an endpoint spike or observation tick', async metric => {
    const context = drawing()
    const colors: string[] = []
    context.fillRect.mockImplementation(() => { colors.push(context.fillStyle) })
    const wrapper = mount(UsageTrace, { props: { metric, samples: [{
      ...sample(2), at: '2026-11-08T01:03:00Z', intervals: [{ kind: UsageIntervalOutKind.batched, start_at: '2026-11-08T01:00:00Z', input: 600, remaining_input: 300, output: 300, total: 900, cache_read: 300, cache_write: null, reasoning: null }],
    }] } })
    await frame()
    expect(wrapper.attributes('data-state')).toBe('bars')
    expect(context.fillRect).toHaveBeenCalledTimes(3)
    expect(context.fillRect.mock.calls.map(call => call[3])).toEqual([12, 12, 12])
    expect(new Set(colors).size).toBe(1)
    expect(wrapper.attributes('aria-label')).toContain('300 tokens; consumption time unavailable')
    expect(wrapper.attributes('aria-label')).toContain('interval average')
    expect(wrapper.attributes('title')).toBe(wrapper.attributes('aria-label'))
    expect(wrapper.attributes('aria-label')).not.toContain('Neutral ticks')
    wrapper.unmount()
  })
  it.each(['input', 'cache_read', 'output'] as const)('draws only tiny/high measured %s bars despite co-located baseline/batch/reset observations', async metric => {
    const context = drawing()
    const observations = ['baseline', 'batched', 'reset'].map(kind => ({ kind, at: 0, startAt: null, value: 1000000 }))
    const geometry = { state: 'bars' as const, maximum: 1000, endAt: 180000, startAt: 0, baselineAt: 0, buckets: [
      { at: 0, value: 1, observations }, { at: 60000, value: 1000, observations },
      { at: 120000, value: 0, observations }, { at: 180000, value: null, observations },
    ] }
    const before = structuredClone(geometry)
    const wrapper = mount(UsageTrace, { props: { metric, samples: [], preparedGeometry: geometry } })
    await frame()
    expect(context.fillRect.mock.calls.map(call => call[3])).toEqual([1, 12])
    expect(wrapper.attributes('aria-label')).toContain('1,000,000 tokens; consumption time unavailable')
    expect(geometry).toEqual(before)
  })
})
