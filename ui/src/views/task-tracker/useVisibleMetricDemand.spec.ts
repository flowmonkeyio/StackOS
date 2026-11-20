import { describe, expect, it } from 'vitest'
import { METRIC_OVERSCAN_PX, visibleMetricKeys } from './useVisibleMetricDemand'

describe('explicit viewport metric demand', () => {
  it('keeps horizontal exclusions and vertical overscan bounded as hidden work grows', () => {
    const root = document.createElement('section')
    root.getBoundingClientRect = () => ({ left: 0, right: 1000, top: 0, bottom: 600, width: 1000, height: 600 }) as DOMRect
    function card(key: string, x: number, y: number) {
      const node = document.createElement('div')
      node.dataset.taskKey = key
      node.getBoundingClientRect = () => ({ left: x, right: x + 300, top: y, bottom: y + 100, width: 300, height: 100 }) as DOMRect
      root.append(node)
    }
    for (let i = 0; i < 5; i++) card(`visible-${i}`, 0, i * 110)
    card('overscan', 0, 600 + METRIC_OVERSCAN_PX - 1)
    card('below', 0, 600 + METRIC_OVERSCAN_PX)
    for (let i = 0; i < 5000; i++) card(`excluded-${i}`, 1200, 0)
    expect(visibleMetricKeys(root, '[data-task-key]', node => node.dataset.taskKey))
      .toEqual(['visible-0', 'visible-1', 'visible-2', 'visible-3', 'visible-4', 'overscan'])
  })
})
