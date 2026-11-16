import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { TaskDeliveryStateOutStatus, UsageIntervalOutKind, type SchemaActiveTasksOut, type SchemaUsageSampleOut } from '@/api'
import { callOperation } from '@/lib/operations'
import { openTrackerStatusStream } from '@/lib/task-tracker/liveEvents'
import { useActiveTasks } from '@/composables/useActiveTasks'
import { activeTaskFixture, activeTasksFixture } from './activeTaskFixtures'
import { agentStatsFixture } from './agentStatsFixtures'
import ActiveTaskRow from './ActiveTaskRow.vue'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/task-tracker/liveEvents', async original => ({ ...await original<object>(), openTrackerStatusStream: vi.fn(() => ({ close: vi.fn() })) }))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('transitions accepted snapshot changes through the real Active task reader without animating stale or unrelated updates', async () => {
  vi.useFakeTimers({ toFake: ['Date'] }).setSystemTime(new Date('2026-11-08T01:02:10Z'))
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  vi.stubGlobal('IntersectionObserver', undefined)
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} })))
  const frames = new Map<number, FrameRequestCallback>()
  let frameId = 0
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id))
  const paint = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)) }
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(100)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(16)
  const draws = vi.fn()
  const bars = vi.fn()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ setTransform() {}, clearRect: draws, setLineDash() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fillRect: bars } as unknown as CanvasRenderingContext2D)
  const cancel = vi.fn(), animate = vi.fn(() => ({ cancel, onfinish: null }))
  const originalAnimate = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, 'animate')
  Object.defineProperty(HTMLCanvasElement.prototype, 'animate', { configurable: true, value: animate })
  const page = (input: number, title = 'Measured work') => {
    const samples: SchemaUsageSampleOut[] = [100, 104, input].map((value, index) => ({
      at: `2026-11-08T01:0${index}:00Z`, input: value, cache_read: 80, output: 10 + index,
      total: value + 10 + index, remaining_input: value - 80, cache_write: null, reasoning: null, source: null, intervals: null,
    }))
    const metrics = agentStatsFixture().totals
    return activeTasksFixture(null, [activeTaskFixture({ title,
      delivery: { status: TaskDeliveryStateOutStatus.running, ticketKey: 'child', reason: null },
      active_execution: { invocation_id: 'current', ticket_key: 'child', agent: 'sdlc_delivery', host_key: 'codex', model: 'exact', started_at: samples[0]!.at, live_usage: null },
      agent_stats: { ...metrics, samples, reported_usage: { ...metrics.reported_usage, input, remaining_input: input - 80, cache_read: 80, output: 12, total: input + 12 } },
    })])
  }
  vi.mocked(callOperation).mockResolvedValue(page(110))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] })
  await router.push('/')
  let reader!: ReturnType<typeof useActiveTasks>
  const wrapper = mount(defineComponent({ setup() {
    reader = useActiveTasks(null)
    return () => reader.items.value[0] ? h(ActiveTaskRow, { task: reader.items.value[0], global: true }) : null
  } }), { global: { plugins: [router, createPinia()] } })
  try {
    const stream = vi.mocked(openTrackerStatusStream).mock.calls[0]![0]
    stream.onFrame?.(Date.now()); stream.onUsage?.({ tasks: [], tickets: [], replace: true })
    await flushPromises(); paint()
    expect(reader.feed.value.status).toBe('live')
    expect(animate).not.toHaveBeenCalled() // initial drawing
    const canvas = wrapper.get('canvas').element
    const refresh = async (input: number, title?: string) => {
      let finish!: (response: SchemaActiveTasksOut) => void
      vi.mocked(callOperation).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
      const pending = reader.refresh()
      await flushPromises(); paint()
      expect(wrapper.get('.usage-live-indicator').text()).toBe('Live')
      const count = animate.mock.calls.length
      finish(page(input, title))
      await pending; await nextTick(); paint()
      expect(reader.feed.value.status).toBe('live')
      expect(wrapper.get('canvas').element).toBe(canvas)
      return animate.mock.calls.length - count
    }
    expect(await refresh(120)).toBe(1)
    expect(animate.mock.calls[0]).toEqual([[{ opacity: 0.65 }, { opacity: 1 }], expect.objectContaining({ duration: 160 })])
    expect(await refresh(120, 'Title only')).toBe(0)
    expect(cancel).not.toHaveBeenCalled() // a compatible refresh does not interrupt the accepted drawing
    expect(await refresh(130, 'Title only')).toBe(1)
    // The real reader accepts the API envelope with per-invocation sparse
    // intervals, then the retained list consumer paints all three metrics.
    const sparse = page(130, 'Sparse measured work')
    const task = sparse.items[0]!
    task.active_execution = null
    task.agent_stats!.samples = [
      { at: '2026-11-08T01:02:24Z', input: 390633, remaining_input: 361, cache_read: 390272, output: 3604, total: 394237, cache_write: null, reasoning: null, source: null,
        intervals: [{ kind: UsageIntervalOutKind.batched, start_at: '2026-11-08T01:00:00Z', input: 390633, remaining_input: 361, cache_read: 390272, output: 3604, total: 394237, cache_write: null, reasoning: null }] },
      { at: '2026-11-08T01:06:02Z', input: 785644, remaining_input: 1260, cache_read: 784384, output: 9629, total: 795273, cache_write: null, reasoning: null, source: null,
        intervals: [{ kind: UsageIntervalOutKind.batched, start_at: '2026-11-08T01:02:24Z', input: 395011, remaining_input: 899, cache_read: 394112, output: 6025, total: 401036, cache_write: null, reasoning: null }] },
    ]
    vi.mocked(callOperation).mockResolvedValueOnce(sparse)
    bars.mockClear()
    await reader.refresh(); await nextTick(); paint()
    expect(wrapper.findAll('canvas').map(item => item.attributes('data-state'))).toEqual(['bars', 'bars', 'bars'])
    expect(bars).toHaveBeenCalledTimes(21) // seven overlapping minutes per metric
    expect(wrapper.findAll('canvas').every(item => item.attributes('title')?.includes('interval average'))).toBe(true)
    expect(wrapper.get('dd').text()).toBe('50') // lifetime total is independent of chart deltas
    const beforeUnmount = animate.mock.calls.length
    let finishLate!: (response: SchemaActiveTasksOut) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(resolve => { finishLate = resolve }))
    const pending = reader.refresh()
    await flushPromises(); paint()
    wrapper.unmount(); draws.mockClear()
    finishLate(page(140)); await pending; await nextTick(); paint()
    expect(animate).toHaveBeenCalledTimes(beforeUnmount)
    expect(draws).not.toHaveBeenCalled()
    expect(frames.size).toBe(0)
    expect(callOperation).toHaveBeenCalledTimes(6) // one bulk call per actual refresh
  } finally {
    if (wrapper.exists()) wrapper.unmount()
    if (originalAnimate) Object.defineProperty(HTMLCanvasElement.prototype, 'animate', originalAnimate)
    else delete (HTMLCanvasElement.prototype as unknown as { animate?: unknown }).animate
  }
})
