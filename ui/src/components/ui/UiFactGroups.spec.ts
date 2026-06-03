import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import UiFactGroups from './UiFactGroups.vue'

describe('UiFactGroups', () => {
  it('renders grouped fact sections with semantic labels and values', () => {
    const wrapper = mount(UiFactGroups, {
      props: {
        ariaLabel: 'Action summary',
        groups: [
          {
            title: 'Execution Target',
            description: 'Provider and operation.',
            items: [
              { label: 'Provider', value: 'slack-bot', emphasis: 'strong' },
              { label: 'Operation', value: 'message.send', mono: true, wide: true },
            ],
          },
          {
            title: 'Outcome',
            items: [
              { label: 'Dry run', value: false, badge: true },
              { label: 'Run', value: null },
            ],
          },
        ],
      },
    })

    expect(wrapper.attributes('aria-label')).toBe('Action summary')
    expect(wrapper.text()).toContain('Execution Target')
    expect(wrapper.text()).toContain('Provider and operation.')
    expect(wrapper.text()).toContain('slack-bot')
    expect(wrapper.text()).toContain('message.send')
    expect(wrapper.text()).toContain('Dry run')
    expect(wrapper.text()).toContain('No')
    expect(wrapper.text()).toContain('Run')
    expect(wrapper.text()).toContain('—')
    expect(wrapper.find('.ui-badge').exists()).toBe(true)
  })
})
