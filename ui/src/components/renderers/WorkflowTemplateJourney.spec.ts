import { mount, type VueWrapper } from '@vue/test-utils'
import type { Component } from 'vue'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'

import type { SchemaWorkflowTemplateSpec } from '@/api'

let WorkflowTemplateJourney: Component

beforeAll(async () => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    },
  )
  WorkflowTemplateJourney = (await import('./WorkflowTemplateJourney.vue')).default
})

afterAll(() => {
  vi.unstubAllGlobals()
})

function workflowSpec(stepCount: number): SchemaWorkflowTemplateSpec {
  return {
    key: 'engineering.tracked-delivery',
    name: 'Engineering Tracked Delivery',
    description: 'Deliver engineering work through a tracked workflow.',
    steps: Array.from({ length: stepCount }, (_, index) => ({
      id: `step-${index + 1}`,
      title: `Step ${index + 1}`,
      purpose: `Complete stage ${index + 1}.`,
    })),
  } as SchemaWorkflowTemplateSpec
}

function mountJourney(stepCount: number): VueWrapper {
  return mount(WorkflowTemplateJourney, {
    props: { spec: workflowSpec(stepCount) },
    global: {
      stubs: {
        VueFlow: {
          name: 'VueFlow',
          props: {
            nodes: { type: Array, default: () => [] },
            zoomOnScroll: { type: Boolean, default: true },
            zoomOnPinch: { type: Boolean, default: true },
            panOnScroll: { type: Boolean, default: false },
            preventScrolling: { type: Boolean, default: true },
          },
          template:
            '<div class="vue-flow-stub"><slot /><slot v-for="node in nodes" name="node-journey-step" :data="node.data" :target-position="node.targetPosition" :source-position="node.sourcePosition" /></div>',
        },
        Background: true,
        Controls: true,
        Handle: true,
      },
    },
  })
}

describe('WorkflowTemplateJourney', () => {
  it('lets wheel events scroll the page instead of resizing the graph', () => {
    const wrapper = mountJourney(12)
    const flow = wrapper.getComponent({ name: 'VueFlow' })

    expect(flow.props('zoomOnScroll')).toBe(false)
    expect(flow.props('zoomOnPinch')).toBe(false)
    expect(flow.props('panOnScroll')).toBe(false)
    expect(flow.props('preventScrolling')).toBe(false)
  })

  it('grows the graph canvas to fit every workflow row', () => {
    expect(mountJourney(3).get('.workflow-journey__canvas').attributes('style')).toContain(
      'height: 430px',
    )
    expect(mountJourney(12).get('.workflow-journey__canvas').attributes('style')).toContain(
      'height: 760px',
    )
  })

  it('exposes desktop workflow stages as keyboard-operable buttons', async () => {
    const wrapper = mountJourney(3)
    const stageButtons = wrapper.findAll('.workflow-journey-node')

    expect(stageButtons).toHaveLength(3)
    expect(stageButtons[0]?.element.tagName).toBe('BUTTON')
    expect(stageButtons[0]?.attributes('aria-pressed')).toBe('true')

    await stageButtons[1]?.trigger('click')

    expect(wrapper.findAll('.workflow-journey-node')[1]?.attributes('aria-pressed')).toBe('true')
    expect(wrapper.text()).toContain('Step 2')
  })
})
