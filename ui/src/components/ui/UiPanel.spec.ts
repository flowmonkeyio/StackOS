import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import UiPanel from './UiPanel.vue'

describe('UiPanel', () => {
  it('applies default padding and supports the explicit unpadded escape hatch', () => {
    const padded = mount(UiPanel, {
      slots: {
        default: 'Panel',
      },
    })
    expect(padded.classes()).toContain('p-3')

    const unpadded = mount(UiPanel, {
      props: {
        padded: false,
      },
      slots: {
        default: 'Panel',
      },
    })
    expect(unpadded.classes()).not.toContain('p-3')
  })
})
