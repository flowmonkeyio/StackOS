import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import UiMetadataStrip from './UiMetadataStrip.vue'

describe('UiMetadataStrip', () => {
  it('renders repeated labels without dropping values', () => {
    const wrapper = mount(UiMetadataStrip, {
      props: {
        items: [
          { label: 'Run', value: '#1' },
          { label: 'Run', value: '#2' },
        ],
      },
    })

    expect(wrapper.text()).toContain('#1')
    expect(wrapper.text()).toContain('#2')
  })
})
