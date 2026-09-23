import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import NativeAccountSessionPanel from './NativeAccountSessionPanel.vue'

describe('NativeAccountSessionPanel', () => {
  it('shows the durable shared-session state and warns when other projects are affected', () => {
    const wrapper = mount(NativeAccountSessionPanel, {
      props: {
        busy: false,
        state: {
          credential_ref: 'cred_telegram',
          provider_key: 'telegram',
          desired_connected: true,
          connected: false,
          status: 'repair-required',
          project_ids: [1, 2],
          affects_other_projects: true,
          next_action: 'Review the Telegram Account setup, then connect again.',
        },
      },
    })

    expect(wrapper.text()).toContain('Reconnect needed')
    expect(wrapper.text()).toContain('other projects')
    expect(wrapper.text()).toContain('Review the Telegram Account setup')
    expect(wrapper.text()).toContain('Disconnect stops TDLib but preserves saved authorization')
    expect(wrapper.text()).toContain('disconnect first, save the changes, then connect again')
    expect(wrapper.get('button').text()).toContain('Disconnect session')
  })

  it('emits explicit connection controls without treating authorization as a session action', async () => {
    const wrapper = mount(NativeAccountSessionPanel, {
      props: {
        busy: false,
        state: {
          credential_ref: 'cred_telegram',
          provider_key: 'telegram',
          desired_connected: true,
          connected: true,
          status: 'connected',
          project_ids: [1],
          affects_other_projects: false,
          next_action: null,
        },
      },
    })

    await wrapper.get('button').trigger('click')
    await wrapper.findAll('button')[1]!.trigger('click')

    expect(wrapper.emitted('disconnect')).toEqual([[]])
    expect(wrapper.emitted('refresh')).toEqual([[]])
    expect(wrapper.text()).not.toContain('authorization code')
  })

  it('explains an unauthorized user Account keeps the original connect request', () => {
    const wrapper = mount(NativeAccountSessionPanel, {
      props: {
        busy: false,
        state: {
          credential_ref: 'cred_user',
          provider_key: 'telegram',
          desired_connected: true,
          connected: false,
          status: 'authorization_required',
          project_ids: [1],
          affects_other_projects: false,
          next_action: 'Complete local Telegram sign-in after disconnecting this session.',
        },
      },
    })

    expect(wrapper.text()).toContain('Sign-in required')
    expect(wrapper.text()).toContain('the original connection request remains in effect')
    expect(wrapper.get('button').text()).toContain('Disconnect session')
  })

  it('does not connect while edited Account changes are unsaved, then connects after they are clean', async () => {
    const wrapper = mount(NativeAccountSessionPanel, {
      props: {
        busy: false,
        dirty: true,
        state: {
          credential_ref: 'cred_bot',
          provider_key: 'telegram',
          desired_connected: false,
          connected: false,
          status: 'disconnected',
          project_ids: [1],
          affects_other_projects: false,
          next_action: null,
        },
      },
    })

    const connect = wrapper.get('button')
    expect(connect.attributes('disabled')).toBeDefined()
    await connect.trigger('click')
    expect(wrapper.emitted('connect')).toBeUndefined()

    await wrapper.setProps({ dirty: false })
    await connect.trigger('click')
    expect(wrapper.emitted('connect')).toEqual([[]])
  })

  it('allows a dirty connected Account to disconnect before saving changed settings', async () => {
    const wrapper = mount(NativeAccountSessionPanel, {
      props: {
        busy: false,
        dirty: true,
        state: {
          credential_ref: 'cred_user',
          provider_key: 'telegram',
          desired_connected: true,
          connected: true,
          status: 'connected',
          project_ids: [1],
          affects_other_projects: false,
          next_action: null,
        },
      },
    })

    const disconnect = wrapper.get('button')
    expect(disconnect.text()).toContain('Disconnect session')
    expect(disconnect.attributes('disabled')).toBeUndefined()
    await disconnect.trigger('click')
    expect(wrapper.emitted('disconnect')).toEqual([[]])
  })
})
