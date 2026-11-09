import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SchemaUsageSampleOut } from '@/api'
import UsageTrace from './UsageTrace.vue'

const sample = (second: number): SchemaUsageSampleOut => ({
  at: `2026-11-08T01:00:0${second}Z`, input: 100 + second * 2, cache_read: 80,
  output: 5 + second, total: 105 + second * 3, cache_write: null, reasoning: null, remaining_input: null,
})
function drawing() {
  const context = { setTransform: vi.fn(), clearRect: vi.fn(), setLineDash: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(), fill: vi.fn(), strokeStyle: '', fillStyle: '' }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(64)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(16)
  vi.stubGlobal('devicePixelRatio', 2)
  return context
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.documentElement.removeAttribute('data-theme') })

describe('measured mini-canvas', () => {
  it('draws an empty dash, a first dot and measured steps without changing the canvas slot', async () => {
    const context = drawing()
    const wrapper = mount(UsageTrace, { props: { samples: [], metric: 'input' } })
    const canvas = wrapper.get('canvas').element as HTMLCanvasElement
    expect([canvas.width, canvas.height]).toEqual([128, 32])
    expect(context.setTransform).toHaveBeenLastCalledWith(2, 0, 0, 2, 0, 0)
    expect(context.setLineDash).toHaveBeenLastCalledWith([2, 3])
    expect(context.lineTo).toHaveBeenLastCalledWith(62, 8)
    expect(context.arc).not.toHaveBeenCalled()
    await wrapper.setProps({ samples: [sample(0)] })
    expect(context.arc).toHaveBeenLastCalledWith(32, 8, 1.7, 0, Math.PI * 2)
    expect(context.fill).toHaveBeenCalledOnce()
    context.moveTo.mockClear(); context.lineTo.mockClear()
    await wrapper.setProps({ samples: [sample(0), sample(2)] })
    expect(context.moveTo.mock.calls).toEqual([[64 / 28, 11 / 12 * 16]])
    expect(context.lineTo.mock.calls).toEqual([[27 / 28 * 64, 11 / 12 * 16], [27 / 28 * 64, 16 / 12]])
    const before = context.clearRect.mock.calls.length
    await wrapper.setProps({ samples: [sample(0), sample(2), sample(5)] })
    expect(context.clearRect.mock.calls.length).toBeGreaterThan(before)
    expect(wrapper.get('canvas').element).toBe(canvas)
    expect(wrapper.find('svg').exists()).toBe(false)
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
    const color = vi.spyOn(window, 'getComputedStyle').mockReturnValue({ color: 'rgb(1, 2, 3)' } as CSSStyleDeclaration)
    const wrapper = mount(UsageTrace, { props: { samples: [sample(0)], metric: 'output' } })
    color.mockReturnValue({ color: 'rgb(4, 5, 6)' } as CSSStyleDeclaration)
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
