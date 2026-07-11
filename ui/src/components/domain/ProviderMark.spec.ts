import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ProviderMark from './ProviderMark.vue'

describe('ProviderMark', () => {
  it('renders a canonical provider logo when mapped', () => {
    const wrapper = mount(ProviderMark, {
      props: { name: 'Firecrawl', providerKey: 'firecrawl', pluginSlug: 'utils' },
    })

    expect(wrapper.get('img').attributes('src')).toBe('/images/integrations/firecrawl-icon.png')
  })

  it('renders stable initials when no logo is mapped', () => {
    const wrapper = mount(ProviderMark, {
      props: { name: 'Example Provider', providerKey: 'example-provider' },
    })

    expect(wrapper.text()).toBe('EP')
    expect(wrapper.find('img').exists()).toBe(false)
  })
})
