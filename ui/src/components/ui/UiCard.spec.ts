import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import UiCard from './UiCard.vue'

describe('UiCard', () => {
  it('applies default body padding and elevation', () => {
    const wrapper = mount(UiCard, {
      slots: {
        default: 'Body',
      },
    })

    expect(wrapper.classes()).toContain('shadow-xs')
    expect(wrapper.get('.ui-card__body').classes()).toContain('p-4')
  })

  it('supports comfortable density and explicit no-padding surfaces', () => {
    const comfortable = mount(UiCard, {
      props: {
        density: 'comfortable',
      },
      slots: {
        default: 'Body',
      },
    })
    expect(comfortable.get('.ui-card__body').classes()).toContain('p-5')

    const unpadded = mount(UiCard, {
      props: {
        padded: false,
        elevated: false,
      },
      slots: {
        default: 'Body',
      },
    })
    expect(unpadded.classes()).not.toContain('shadow-xs')
    expect(unpadded.get('.ui-card__body').classes()).not.toContain('p-4')
    expect(unpadded.get('.ui-card__body').classes()).not.toContain('p-5')
  })
})
