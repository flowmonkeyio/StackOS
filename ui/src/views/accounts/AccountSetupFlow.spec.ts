import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AccountSetupFlow from './AccountSetupFlow.vue'

describe('AccountSetupFlow', () => {
  it('announces the current and completed steps in a labelled, ordered progress list', () => {
    const wrapper = mount(AccountSetupFlow, {
      props: {
        ariaLabel: 'Telegram Account setup',
        steps: [
          { key: 'details', label: 'Account details', state: 'complete' },
          { key: 'sign-in', label: 'Sign in', state: 'current' },
          { key: 'ready', label: 'Ready', state: 'upcoming' },
        ],
      },
      slots: { default: '<div data-test="stage">Phone number</div>' },
    })

    const progress = wrapper.get('nav[aria-label="Telegram Account setup"]')
    expect(progress.findAll('ol > li')).toHaveLength(3)
    expect(progress.get('[aria-current="step"]').text()).toBe('Sign in')
    expect(progress.text()).toContain('Completed: Account details')
    expect(progress.text()).toContain('Ready')
    expect(wrapper.get('[data-test="stage"]').text()).toBe('Phone number')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('updates the visible progress without replacing the provider-owned stage content', async () => {
    const wrapper = mount(AccountSetupFlow, {
      props: {
        steps: [
          { key: 'details', label: 'Account details', state: 'current' },
          { key: 'verify', label: 'Verification', state: 'upcoming', optional: true },
        ],
      },
      slots: { default: '<input aria-label="Account name" />' },
    })

    const input = wrapper.get<HTMLInputElement>('input[aria-label="Account name"]')
    await input.setValue('Telegram – Default')
    expect(wrapper.get('nav').attributes('aria-label')).toBe('Account setup progress')
    expect(wrapper.get('nav li:nth-child(2)').text()).toContain('Verification')
    expect(wrapper.get('nav li:nth-child(2)').text()).toContain('Optional')

    await wrapper.setProps({
      steps: [
        { key: 'details', label: 'Account details', state: 'complete' },
        { key: 'verify', label: 'Verification', state: 'current', optional: true },
      ],
    })

    expect(wrapper.get('[aria-current="step"]').text()).toContain('Verification')
    expect(wrapper.get<HTMLInputElement>('input[aria-label="Account name"]').element).toBe(
      input.element,
    )
    expect(input.element.value).toBe('Telegram – Default')
  })

  it('renders only the Account content for a provider without staged setup', () => {
    const wrapper = mount(AccountSetupFlow, {
      props: { steps: [] },
      slots: { default: '<p>Bot token</p>' },
    })

    expect(wrapper.find('nav').exists()).toBe(false)
    expect(wrapper.text()).toBe('Bot token')
  })
})
