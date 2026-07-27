import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ProviderMark from './ProviderMark.vue'

describe('ProviderMark', () => {
  it('renders a canonical provider logo when mapped', () => {
    const wrapper = mount(ProviderMark, {
      props: { name: 'Firecrawl', providerKey: 'firecrawl', pluginSlug: 'utils' },
    })

    expect(wrapper.get('img').attributes('src')).toBe('/images/integrations/firecrawl-icon.png')
    expect(wrapper.classes()).toContain('provider-mark--logo')
    expect(wrapper.classes()).not.toContain('provider-mark--dark')
    expect(wrapper.classes()).not.toContain('provider-mark--inverse')
  })

  it('renders a light wordmark on the shared dark provider tile', () => {
    const wrapper = mount(ProviderMark, {
      props: { name: 'FTP / Explicit FTPS', providerKey: 'ftp', pluginSlug: 'utils' },
    })

    expect(wrapper.get('img').attributes('src')).toBe('/images/integrations/ftp.svg')
    expect(wrapper.classes()).toContain('provider-mark--wordmark')
    expect(wrapper.classes()).toContain('provider-mark--dark')
  })

  it('renders the Amazon S3 mark through the shared provider mapping', () => {
    const wrapper = mount(ProviderMark, {
      props: { name: 'Amazon S3', providerKey: 'aws-s3', pluginSlug: 'utils' },
    })

    expect(wrapper.get('img').attributes('src')).toBe('/images/integrations/s3.png')
    expect(wrapper.classes()).toContain('provider-mark--logo')
    expect(wrapper.classes()).not.toContain('provider-mark--wordmark')
    expect(wrapper.classes()).not.toContain('provider-mark--dark')
  })

  it('renders the official white Linear wordmark on a contrast-safe tile', () => {
    const wrapper = mount(ProviderMark, {
      props: { name: 'Linear', providerKey: 'linear', pluginSlug: 'linear', size: 'xs' },
    })

    expect(wrapper.get('img').attributes('src')).toBe('/images/integrations/linear-logo-white.png')
    expect(wrapper.classes()).toContain('provider-mark--wordmark')
    expect(wrapper.classes()).toContain('provider-mark--inverse')
    expect(wrapper.classes()).toContain('provider-mark--xs')
  })

  it('renders stable initials when no logo is mapped', () => {
    const wrapper = mount(ProviderMark, {
      props: { name: 'Example Provider', providerKey: 'example-provider' },
    })

    expect(wrapper.text()).toBe('EP')
    expect(wrapper.find('img').exists()).toBe(false)
  })
})
