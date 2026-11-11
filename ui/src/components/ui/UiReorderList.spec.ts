import { defineComponent, nextTick, type PropType } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import UiReorderList from './UiReorderList.vue'
import UiReorderHandle from './UiReorderHandle.vue'
import reorderSource from './UiReorderList.vue?raw'
import boardSource from '@/views/task-tracker/TrackerBoardPanel.vue?raw'

// Deliberately no task, status, store or API types: the future consumer supplies
// its own eligibility revisions and persists the neutral neighbor intent.
interface Entry { slug: string; name: string; revision: string }
const entries = (count = 3): Entry[] => Array.from({ length: count }, (_, i) => ({ slug: `entry-${i}`, name: `Entry ${i}`, revision: '1' }))
const Fixture = defineComponent({
  components: { UiReorderList, UiReorderHandle },
  props: { items: { type: Array as PropType<Entry[]>, required: true }, scope: { type: String, default: 'one' }, disabled: Boolean },
  emits: ['move'],
  template: `<UiReorderList :items="items" :item-key="item => item.slug" :item-label="item => item.name" :item-version="item => item.revision" :scope-key="scope" :disabled="disabled" @move="$emit('move', $event)">
    <template #default="{ item, handle }"><UiReorderHandle v-bind="handle" /><button class="details">{{ item.name }}</button></template>
  </UiReorderList>`,
})
let callbacks: Map<number, FrameRequestCallback>
let nextFrame: number
const wrappers: ReturnType<typeof mount>[] = []
const styles: HTMLStyleElement[] = []
beforeEach(() => {
  callbacks = new Map(); nextFrame = 0
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { callbacks.set(++nextFrame, callback); return nextFrame }))
  vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => callbacks.delete(id)))
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); styles.splice(0).forEach(style => style.remove()); vi.restoreAllMocks(); vi.unstubAllGlobals() })

// JSDOM does not lay out pseudo-elements. Resolve the actual source CSS rules
// against the live insertion target, then calculate its clipping range using
// the real generic/Board gap and padding. This is source geometry, not browser proof.
function markerProperty(node: HTMLElement, pseudo: 'before' | 'after', property: string, sheet: CSSStyleSheet): string {
  let value = ''
  for (const rule of Array.from(sheet.cssRules) as CSSStyleRule[]) {
    if (rule.selectorText?.split(',').some(selector => selector.trim().endsWith(`::${pseudo}`)
      && node.matches(selector.trim().replace(`::${pseudo}`, '')))) value = rule.style.getPropertyValue(property) || value
  }
  return value
}
function markerOffset(value: string, gap: number): number {
  if (value === 'calc(var(--reorder-gap, 8px) / -2)') return -gap / 2
  expect(value).toMatch(/^-?\d+(?:px)?$/)
  return parseFloat(value)
}
function render(count = 3) {
  const wrapper = mount(Fixture, { attachTo: document.body, props: { items: entries(count) } })
  wrappers.push(wrapper)
  const list = wrapper.get<HTMLElement>('.ui-reorder-list')
  vi.spyOn(list.element, 'getBoundingClientRect').mockReturnValue({ top: 0, bottom: 300, left: 0, right: 300, height: 300, width: 300 } as DOMRect)
  Object.defineProperties(list.element, { scrollHeight: { value: count * 100, configurable: true }, clientHeight: { value: 300, configurable: true } })
  wrapper.findAll<HTMLElement>('[data-reorder-key]').forEach(node => {
    vi.spyOn(node.element, 'getBoundingClientRect').mockImplementation(() => {
      const i = wrapper.props('items').findIndex(item => item.slug === node.attributes('data-reorder-key'))
      return { top: i * 100 - list.element.scrollTop, bottom: (i + 1) * 100 - list.element.scrollTop, left: 0, right: 300, height: 100, width: 300 } as DOMRect
    })
  })
  const transfer = { effectAllowed: '', dropEffect: '', setData: vi.fn(), setDragImage: vi.fn() }
  return { wrapper, list, transfer, lift: (index = 0) => wrapper.findAll('[data-reorder-handle]')[index]!.trigger('dragstart', { dataTransfer: transfer }) }
}
async function tick(time: number) {
  const pending = [...callbacks.values()]
  callbacks.clear()
  pending.forEach(callback => callback(time))
  await nextTick()
}

describe('neutral vertical list reorder', () => {
  it.each(['generic', 'Board'] as const)('keeps %s boundary markers inside the scroll viewport without moving the items', async consumer => {
    const style = document.createElement('style')
    style.textContent = [reorderSource, ...(consumer === 'Board' ? [boardSource] : [])]
      .map(source => source.match(/<style scoped>([\s\S]*?)<\/style>/)![1]).join('\n')
    document.head.append(style); styles.push(style)
    const { wrapper, list, transfer, lift } = render()
    if (consumer === 'Board') list.element.classList.add('tracker-board__cards')
    const computed = getComputedStyle(list.element)
    const gap = parseFloat(computed.getPropertyValue('--reorder-gap')) || 8
    const top = parseFloat(computed.paddingTop) || 0
    const bottom = parseFloat(computed.paddingBottom) || 0
    const height = consumer === 'Board' ? 224 : 100
    const scrollHeight = top + 3 * height + 2 * gap + bottom
    Object.defineProperty(list.element, 'scrollHeight', { value: scrollHeight, configurable: true })
    const nodes = wrapper.findAll<HTMLElement>('[data-reorder-key]')
    nodes.forEach((node, i) => vi.mocked(node.element.getBoundingClientRect).mockImplementation(() => ({
      top: top + i * (height + gap) - list.element.scrollTop,
      bottom: top + i * (height + gap) + height - list.element.scrollTop,
      left: 0, right: 300, width: 300, height,
    } as DOMRect)))
    await lift(2)
    await list.trigger('dragover', { dataTransfer: transfer, clientY: top })
    const first = wrapper.get<HTMLElement>('.ui-reorder-list__before').element
    const firstY = first.getBoundingClientRect().top + markerOffset(markerProperty(first, 'before', 'top', style.sheet!), gap)
    const lineHeight = parseFloat(markerProperty(first, 'before', 'height', style.sheet!))
    expect(markerProperty(first, 'before', 'position', style.sheet!)).toBe('absolute')
    expect(firstY).toBeGreaterThanOrEqual(0)
    expect(firstY + lineHeight).toBeLessThanOrEqual(list.element.clientHeight)
    await list.trigger('dragover', { dataTransfer: transfer, clientY: nodes[1]!.element.getBoundingClientRect().top + 1 })
    const middle = wrapper.get<HTMLElement>('.ui-reorder-list__before').element
    expect(markerOffset(markerProperty(middle, 'before', 'top', style.sheet!), gap)).toBe(-gap / 2)
    list.element.scrollTop = scrollHeight - list.element.clientHeight
    await lift(0)
    await list.trigger('dragover', { dataTransfer: transfer, clientY: 299 })
    const last = wrapper.get<HTMLElement>('.ui-reorder-list__after').element
    const lastY = last.getBoundingClientRect().bottom - markerOffset(markerProperty(last, 'after', 'bottom', style.sheet!), gap)
    expect(lastY - lineHeight).toBeGreaterThanOrEqual(0)
    expect(lastY).toBeLessThanOrEqual(list.element.clientHeight)
    expect(nodes.map(node => node.element.getBoundingClientRect().height)).toEqual([height, height, height])
    expect(list.element.scrollHeight).toBe(scrollHeight)
  })
  it('lifts only through the shared handle, retains source dimensions and emits one neutral move', async () => {
    const { wrapper, list, transfer, lift } = render()
    const bodyDrag = new Event('dragstart', { bubbles: true, cancelable: true })
    wrapper.get('.details').element.dispatchEvent(bodyDrag)
    expect(bodyDrag.defaultPrevented).toBe(true)
    expect(wrapper.find('.ui-reorder-list__placeholder').exists()).toBe(false)
    await lift()
    expect(transfer.effectAllowed).toBe('move')
    expect(transfer.setDragImage).toHaveBeenCalledWith(expect.any(HTMLElement), 16, 8)
    expect(wrapper.get('.ui-reorder-list__preview').text()).toBe('Entry 0')
    expect(wrapper.get('.ui-reorder-list__placeholder').attributes('style')).toContain('height: 100px')
    await list.trigger('dragover', { dataTransfer: transfer, clientX: 100, clientY: 290 })
    expect(wrapper.findAll('.ui-reorder-list__after')).toHaveLength(1)
    expect(wrapper.findAll('.ui-reorder-list__before')).toHaveLength(0)
    await list.trigger('drop', { dataTransfer: transfer, clientX: 100, clientY: 290 })
    await wrapper.get('[data-reorder-handle]').trigger('dragend')
    expect(wrapper.emitted('move')).toEqual([[{ itemKey: 'entry-0', anchorKey: 'entry-2', placement: 'after' }]])
    expect(wrapper.get('[role="status"]').text()).toBe('Moved Entry 0 after Entry 2.')
    expect(wrapper.find('.ui-reorder-list__placeholder').exists()).toBe(false)
    expect(callbacks.size).toBe(0)
  })
  it('supports before and no-op drops without duplicate feedback or a same-position intent', async () => {
    const { wrapper, list, transfer, lift } = render()
    await lift()
    await list.trigger('drop', { dataTransfer: transfer, clientY: 110 })
    expect(wrapper.emitted('move')).toBeUndefined()
    expect(wrapper.get('[role="status"]').text()).toBe('Order unchanged.')
    await lift(2)
    await list.trigger('dragover', { dataTransfer: transfer, clientY: 10 })
    expect(wrapper.findAll('.ui-reorder-list__before')).toHaveLength(1)
    await list.trigger('drop', { dataTransfer: transfer, clientY: 10 })
    expect(wrapper.emitted('move')).toEqual([[{ itemKey: 'entry-2', anchorKey: 'entry-0', placement: 'before' }]])
  })
  it('provides click and keyboard top/up/down/bottom commands, announcements and focus return', async () => {
    const { wrapper } = render()
    const handle = wrapper.findAll('[data-reorder-handle]')[1]!
    await handle.trigger('click')
    expect(wrapper.findAll('[role="menuitem"]').map(item => item.text())).toEqual(['Move up', 'Move down', 'Move to top', 'Move to bottom'])
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await nextTick()
    expect(wrapper.emitted('move')).toEqual([[{ itemKey: 'entry-1', anchorKey: 'entry-2', placement: 'after' }]])
    expect(document.activeElement).toBe(handle.element)
    expect(wrapper.get('[role="status"]').text()).toContain('Moved Entry 1')
    await wrapper.get('[data-reorder-handle]').trigger('click')
    const actions = wrapper.findAll('[role="menuitem"]')
    expect(actions[0]!.attributes('disabled')).toBeDefined()
    expect(actions[2]!.attributes('disabled')).toBeDefined()
  })
  it('restores the handle after pending settles and scrolls only its list to keep a moved item visible', async () => {
    const { wrapper, list } = render(20)
    await wrapper.setProps({ onMove: () => {
      const items = wrapper.props('items')
      void wrapper.setProps({ disabled: true, items: [...items.slice(1), items[0]!] })
    } })
    const handle = wrapper.get('[data-reorder-handle]')
    await handle.trigger('click')
    const bottom = wrapper.findAll('[role="menuitem"]').find(action => action.text() === 'Move to bottom')!
    await bottom.trigger('click')
    await wrapper.setProps({ disabled: false })
    await nextTick()
    expect(document.activeElement).toBe(handle.element)
    expect(list.element.scrollTop).toBe(1700)
  })
  it('continues bounded vertical edge scroll with a stationary pointer and recalculates placement', async () => {
    const { wrapper, list, transfer, lift } = render(20)
    await lift()
    await list.trigger('dragover', { dataTransfer: transfer, clientX: 100, clientY: 299 })
    const initialAnchor = wrapper.get('.ui-reorder-list__before').attributes('data-reorder-key')
    expect(callbacks.size).toBe(1)
    for (let time = 16; time < 1200; time += 16) {
      const before = list.element.scrollTop
      await tick(time)
      expect(list.element.scrollTop - before).toBeLessThanOrEqual(600 * 16 / 1000)
      expect(callbacks.size).toBeLessThanOrEqual(1)
    }
    expect(list.element.scrollTop).toBeGreaterThan(300)
    expect(list.element.scrollLeft).toBe(0)
    expect(wrapper.get('.ui-reorder-list__before').attributes('data-reorder-key')).not.toBe(initialAnchor)
    await list.trigger('dragover', { dataTransfer: transfer, clientX: 100, clientY: 1 })
    const before = list.element.scrollTop
    await tick(1300); await tick(1316)
    expect(list.element.scrollTop).toBeLessThan(before)
    await list.trigger('dragover', { dataTransfer: transfer, clientX: 100, clientY: 150 })
    expect(callbacks.size).toBe(0)
  })
  it('stops at a scroll bound and on leaving the source column without scrolling another list or page', async () => {
    const { list, transfer, lift } = render(20)
    const other = render(20)
    list.element.scrollTop = 1699
    await lift()
    await list.trigger('dragover', { dataTransfer: transfer, clientX: 100, clientY: 299 })
    await tick(16); await tick(32)
    expect(list.element.scrollTop).toBe(1700)
    expect(callbacks.size).toBe(0)
    list.element.scrollTop = 300
    await list.trigger('dragover', { dataTransfer: transfer, clientX: 100, clientY: 299 })
    expect(callbacks.size).toBe(1)
    await other.list.trigger('dragover', { dataTransfer: transfer, clientX: 400, clientY: 299 })
    expect(callbacks.size).toBe(0)
    expect(other.list.element.scrollTop).toBe(0)
    expect(document.documentElement.scrollTop).toBe(0)
    await other.list.trigger('drop', { dataTransfer: transfer })
    await list.trigger('drop', { dataTransfer: transfer, clientY: 250 })
    expect(other.wrapper.emitted('move')).toBeUndefined()
  })
  it('ignores an internal child dragleave with no relatedTarget and stops on the actual list exit', async () => {
    const { wrapper, list, transfer, lift } = render(20)
    await lift()
    await list.trigger('dragover', { dataTransfer: transfer, clientX: 100, clientY: 299 })
    await wrapper.get('.details').trigger('dragleave', { clientX: 100, clientY: 299, relatedTarget: null })
    expect(callbacks.size).toBe(1)
    await list.trigger('dragleave', { clientX: 301, clientY: 299, relatedTarget: null })
    expect(callbacks.size).toBe(0)
  })
  it.each(['Escape', 'dragend', 'outside', 'unmount', 'scope', 'disabled', 'source-removal', 'source-version', 'target-version', 'target-removal'])('cleans all gesture work on %s', async reason => {
    const { wrapper, list, transfer, lift } = render(20)
    await lift()
    await list.trigger('dragover', { dataTransfer: transfer, clientX: 100, clientY: 299 })
    expect(callbacks.size).toBe(1)
    if (reason === 'Escape') document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    if (reason === 'dragend') await wrapper.get('[data-reorder-handle]').trigger('dragend')
    if (reason === 'outside') document.body.dispatchEvent(new Event('drop', { bubbles: true }))
    if (reason === 'unmount') { wrappers.splice(wrappers.indexOf(wrapper), 1); wrapper.unmount() }
    if (reason === 'scope') await wrapper.setProps({ scope: 'two' })
    if (reason === 'disabled') await wrapper.setProps({ disabled: true })
    if (reason.endsWith('removal')) await wrapper.setProps({ items: wrapper.props('items').filter(item => item.slug !== (reason.startsWith('source') ? 'entry-0' : 'entry-3')) })
    if (reason.endsWith('version')) await wrapper.setProps({ items: wrapper.props('items').map(item => item.slug === (reason.startsWith('source') ? 'entry-0' : 'entry-3') ? { ...item, revision: '2' } : item) })
    await nextTick()
    expect(callbacks.size).toBe(0)
    await tick(1000)
    expect(wrapper.emitted('move')).toBeUndefined()
    if (reason !== 'unmount') {
      expect(wrapper.find('.ui-reorder-list__placeholder').exists()).toBe(false)
      await list.trigger('drop', { dataTransfer: transfer, clientY: 290 })
      expect(wrapper.emitted('move')).toBeUndefined()
    }
  })
  it('retains a gesture across unrelated text changes and revalidates current source and target on drop', async () => {
    const { wrapper, list, transfer, lift } = render()
    await lift()
    await list.trigger('dragover', { dataTransfer: transfer, clientY: 290 })
    await wrapper.setProps({ items: wrapper.props('items').map(item => ({ ...item, name: `${item.name} refreshed` })) })
    expect(wrapper.find('.ui-reorder-list__placeholder').exists()).toBe(true)
    await list.trigger('drop', { dataTransfer: transfer, clientY: 10 })
    expect(wrapper.emitted('move')).toBeUndefined() // Actual release is back at the original position.
  })
})
