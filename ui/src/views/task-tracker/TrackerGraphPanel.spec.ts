import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'

import type { TrackerFlowModel } from '@/lib/task-tracker/graphModel'

const flowMocks = vi.hoisted(() => ({
  fitView: vi.fn(async () => undefined),
  setCenter: vi.fn(async () => undefined),
  setViewport: vi.fn(async () => undefined),
}))

vi.mock('@vue-flow/core', () => ({
  useVueFlow: () => flowMocks,
  VueFlow: defineComponent({
    name: 'VueFlow',
    inheritAttrs: false,
    props: {
      nodes: { type: Array, default: () => [] },
      panOnScroll: Boolean,
      zoomOnScroll: { type: Boolean, default: true },
      zoomOnPinch: { type: Boolean, default: true },
      preventScrolling: { type: Boolean, default: true },
    },
    template: '<div data-test="vue-flow"><div v-for="node in nodes" :key="node.id" class="vue-flow__node" tabindex="0" data-test="keyboard-node" @click="$emit(\'nodeClick\', { event: $event, node })">{{ node.id }}</div><slot /></div>',
  }),
}))
vi.mock('@vue-flow/background', () => ({ Background: defineComponent({ template: '<i />' }) }))
vi.mock('@vue-flow/controls', () => ({ Controls: defineComponent({ template: '<i />' }) }))
vi.mock('@vue-flow/minimap', () => ({ MiniMap: defineComponent({ template: '<i />' }) }))

import TrackerGraphPanel from './TrackerGraphPanel.vue'

describe('TrackerGraphPanel interaction and sizing', () => {
  beforeEach(() => vi.clearAllMocks())

  it('keeps the primary graph bounded and zoomable', async () => {
    const wrapper = mount(TrackerGraphPanel, {
      props: {
        flow: {
          nodes: [
            {
              id: 'ticket:one',
              type: 'tracker-ticket',
              position: { x: 0, y: 640 },
              data: {},
            },
          ],
          edges: [],
          warnings: [],
        } as unknown as TrackerFlowModel,
        loading: false,
        flowId: 'tracker-flow-1',
        flowRenderKey: 'render-1',
        graphFitOnInit: false,
        focusNodeIds: [],
        primaryFocusNodeId: null,
        refocusKey: '',
        initialViewport: null,
        activeTaskTitle: 'Task graph',
        activeTaskAvailable: true,
        ticketStatLabel: '1 ticket',
        edgeStatLabel: '0 relations',
        statusRows: [],
        statusFilters: [],
        blockRows: [],
        blockFilters: [],
        filtersActive: false,
        selectionVisible: false,
        selectionLabel: '',
        selectedTicket: null,
        selectedEdgeLabel: null,
        selectionStats: [],
      },
    })

    const flow = wrapper.getComponent({ name: 'VueFlow' })
    expect(flow.props('panOnScroll')).toBe(true)
    expect(flow.props('zoomOnScroll')).toBe(true)
    expect(flow.props('zoomOnPinch')).toBe(true)
    expect(flow.props('preventScrolling')).toBe(true)
    expect(wrapper.get('.tracker-flow-frame').attributes('style')).toBeUndefined()

    await flow.get('[data-test="keyboard-node"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('nodeClick')).toHaveLength(1)
    await flow.get('[data-test="keyboard-node"]').trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('nodeClick')).toHaveLength(2)
    expect(wrapper.emitted('openSelectedDetail')).toBeUndefined()

    await wrapper.setProps({ graphFitOnInit: true, focusNodeIds: ['ticket:one'], primaryFocusNodeId: 'ticket:one', refocusKey: 'first-entry' })
    await flushPromises()
    expect(flowMocks.fitView).not.toHaveBeenCalled()
    flow.vm.$emit('nodes-initialized')
    await flushPromises()
    expect(flowMocks.fitView).toHaveBeenCalledWith(expect.objectContaining({ padding: 0.08 }))
    expect(flowMocks.setCenter).not.toHaveBeenCalled()

    flowMocks.fitView.mockClear()
    await wrapper.setProps({ refocusKey: '', initialViewport: { x: 12, y: 24, zoom: 1.2 } })
    flow.vm.$emit('nodes-initialized')
    await flushPromises()
    expect(flowMocks.fitView).not.toHaveBeenCalled()
    expect(flowMocks.setViewport).toHaveBeenCalledWith({ x: 12, y: 24, zoom: 1.2 }, { duration: 0 })

    flowMocks.fitView.mockClear()
    flowMocks.setCenter.mockClear()
    await wrapper.setProps({ flowRenderKey: 'render-2', refocusKey: 'replacement-graph', initialViewport: null })
    await flushPromises()
    expect(flowMocks.fitView).not.toHaveBeenCalled()
    wrapper.getComponent({ name: 'VueFlow' }).vm.$emit('nodes-initialized')
    await flushPromises()
    expect(flowMocks.fitView).toHaveBeenCalledWith(expect.objectContaining({ padding: 0.08 }))
    flowMocks.fitView.mockClear()

    await wrapper.setProps({
      graphFitOnInit: false,
      refocusKey: 'large-graph-entry',
      flow: {
        nodes: Array.from({ length: 13 }, (_, index) => ({
          id: index === 0 ? 'ticket:one' : `ticket:${index}`, type: 'tracker-ticket',
          position: { x: index * 428, y: 0 }, data: {},
        })), edges: [], warnings: [],
      } as unknown as TrackerFlowModel,
    })
    wrapper.getComponent({ name: 'VueFlow' }).vm.$emit('nodes-initialized')
    await flushPromises()
    expect(flowMocks.fitView).not.toHaveBeenCalled()
    expect(flowMocks.setCenter).toHaveBeenCalled()

    await wrapper.setProps({ loading: true })
    expect(wrapper.text()).toContain('Loading selected graph…')
    expect(wrapper.find('[data-test="vue-flow"]').exists()).toBe(false)
  })
})
