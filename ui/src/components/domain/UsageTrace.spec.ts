import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SchemaUsageSampleOut } from '@/api'
import UsageTrace from './UsageTrace.vue'

const sample = (second: number): SchemaUsageSampleOut => ({
  at: `2026-11-08T01:00:0${second}Z`, input: 100 + second * 2, cache_read: 80,
  output: 5 + second, total: 105 + second * 3, cache_write: null, reasoning: null, remaining_input: null,
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
    const canvas = wrapper.get('canvas').element as HTMLCanvasElement
    expect([canvas.width, canvas.height]).toEqual([64, 32])
    expect(context.setTransform).toHaveBeenLastCalledWith(2, 0, 0, 2, 0, 0)
    expect(context.setLineDash).toHaveBeenLastCalledWith([])
    expect(context.lineTo).toHaveBeenLastCalledWith(31, 14.5)
    expect(context.fillRect).not.toHaveBeenCalled()
    await wrapper.setProps({ samples: [sample(0)] })
    expect(context.lineTo).toHaveBeenLastCalledWith(31, 14.5)
    const [firstX, firstY, firstWidth, firstHeight] = context.fillRect.mock.lastCall!
    expect(firstX).toBeGreaterThan(29.5)
    expect(firstY).toBe(11.5)
    expect(firstWidth).toBeLessThanOrEqual(2)
    expect(firstHeight).toBe(3)
    expect(wrapper.attributes('aria-label')).toContain('First reading establishes a baseline')
    context.fillRect.mockClear()
    await wrapper.setProps({ samples: [sample(0), sample(2)] })
    expect(context.lineTo).toHaveBeenLastCalledWith(31, 14.5)
    expect(context.fillRect).toHaveBeenLastCalledWith(firstX, 2.5, firstWidth, 12)
    expect(wrapper.attributes('role')).toBe('img')
    expect(wrapper.attributes('aria-label')).toContain('4 tokens')
    expect(wrapper.attributes('title')).toBe(wrapper.attributes('aria-label'))
    const before = context.clearRect.mock.calls.length
    await wrapper.setProps({ samples: [sample(0), sample(2), sample(5)] })
    expect(context.clearRect.mock.calls.length).toBeGreaterThan(before)
    expect(wrapper.get('canvas').element).toBe(canvas)
    expect(wrapper.find('svg').exists()).toBe(false)
    wrapper.unmount()
  })

  it('moves an active reporting window only when its shared clock changes', async () => {
    const context = drawing()
    const samples = [sample(0), sample(2)]
    const wrapper = mount(UsageTrace, { props: { samples, metric: 'input' } })
    const canvas = wrapper.element
    const lastBar = context.fillRect.mock.lastCall!
    context.fillRect.mockClear()
    await wrapper.setProps({ endAt: Date.parse(samples[0]!.at) + 60_000 })
    expect(context.fillRect.mock.lastCall![0]).toBeCloseTo(lastBar[0] - 1.5)
    expect(wrapper.element).toBe(canvas)
    await wrapper.setProps({ endAt: undefined })
    expect(context.fillRect.mock.lastCall).toEqual(lastBar)
    wrapper.unmount()
  })

  it('keeps bars slim when a wider canvas or a custom count provides more space', async () => {
    const context = drawing()
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(120)
    const wrapper = mount(UsageTrace, { props: { samples: [sample(0), sample(2)], metric: 'input', barCount: 5 } })
    expect(context.fillRect.mock.lastCall![2]).toBe(2)
    expect(wrapper.attributes('title')).toContain('5 one-minute intervals')
    const canvas = wrapper.element
    await wrapper.setProps({ barCount: 20 })
    expect(context.fillRect.mock.lastCall![2]).toBe(2)
    expect(wrapper.attributes('title')).toContain('20 one-minute intervals')
    expect(wrapper.element).toBe(canvas)
    wrapper.unmount()
  })

  it('redraws for size, theme and DPR, then releases all observers and listeners', async () => {
    const context = drawing()
    let resize!: () => void
    const disconnect = vi.fn()
    vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { resize = callback }; observe() {}; disconnect = disconnect })
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
    const wrapper = mount(UsageTrace, { props: { samples: [sample(0), sample(2)], metric: 'output' } })
    color.mockReturnValue(style('rgb(4, 5, 6)'))
    document.documentElement.setAttribute('data-theme', 'dark')
    await flushPromises()
    expect(context.fillStyle).toBe('rgb(4, 5, 6)')
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(70)
    resize()
    expect((wrapper.element as HTMLCanvasElement).width).toBe(140)
    vi.stubGlobal('devicePixelRatio', 3)
    const changed = queries[0]!.addEventListener.mock.calls[0]![1] as () => void
    changed()
    expect((wrapper.element as HTMLCanvasElement).width).toBe(210)
    expect(queries[0]!.removeEventListener).toHaveBeenCalledWith('change', changed)
    expect(queries).toHaveLength(2)
    const draw = add.mock.calls.find(call => call[0] === 'resize')![1]
    wrapper.unmount()
    expect(disconnect).toHaveBeenCalledOnce()
    expect(mutationDisconnect).toHaveBeenCalledOnce()
    expect(remove).toHaveBeenCalledWith('resize', draw)
    expect(queries[1]!.removeEventListener).toHaveBeenCalledWith('change', changed)
    const count = context.clearRect.mock.calls.length
    window.dispatchEvent(new Event('resize'))
    document.documentElement.setAttribute('data-theme', 'light')
    await flushPromises()
    expect(context.clearRect).toHaveBeenCalledTimes(count)
  })
})
