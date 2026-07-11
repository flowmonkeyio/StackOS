import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
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
      panOnScroll: Boolean,
      zoomOnScroll: { type: Boolean, default: true },
      zoomOnPinch: { type: Boolean, default: true },
      preventScrolling: { type: Boolean, default: true },
    },
    template: '<div data-test="vue-flow"><slot /></div>',
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
  })
})
