import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'

import { usePolling } from './usePolling'

describe('usePolling', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts the immediate task before the first render', () => {
    vi.useFakeTimers()
    const events: string[] = []
    const Probe = defineComponent({
      setup() {
        usePolling(() => {
          events.push('load')
        }, { intervalMs: 1_000 })
        return () => {
          events.push('render')
          return null
        }
      },
    })

    const wrapper = mount(Probe)

    expect(events).toEqual(['load', 'render'])
    wrapper.unmount()
  })
})
