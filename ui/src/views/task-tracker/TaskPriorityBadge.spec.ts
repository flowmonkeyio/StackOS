import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TaskPriorityBadge from './TaskPriorityBadge.vue'
import TaskGraphNode from './TaskGraphNode.vue'
import type { TrackerVueNodeData } from '@/lib/task-tracker/graphModel'

describe('Task priority presentation', () => {
  it.each([['p0', 'danger'], ['p1', 'warning'], ['p2', 'info'], ['p3', 'neutral']])('uses the %s label and semantic tone across task consumers', (priority, tone) => {
    const badge = mount(TaskPriorityBadge, { props: { priority } })
    expect(badge.text()).toBe(priority!.toUpperCase())
    expect(badge.classes()).toContain(`text-${tone}-fg`)
    const graph = mount(TaskGraphNode, { props: { data: { label: 'Task', status: 'in-progress', priorityKey: priority, laneKey: 'implementation' } as TrackerVueNodeData } })
    expect(graph.get(`[aria-label="Priority ${priority!.toUpperCase()}"]`).classes()).toContain(`text-${tone}-fg`)
    badge.unmount()
    graph.unmount()
  })
})
