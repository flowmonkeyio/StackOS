import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import NativeAccountAuthorizationPanel from './NativeAccountAuthorizationPanel.vue'

describe('NativeAccountAuthorizationPanel', () => {
  it('offers bot token verification retry and confirms saved authorization', async () => {
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        accountKind: 'bot',
        state: {
          credential_ref: 'cred_bot',
          provider_key: 'telegram',
          status: 'pending',
          repair_hint: 'Check the saved bot token.',
        },
        mode: 'phone',
        allowsQr: false,
        busy: false,
      },
    })
    expect(wrapper.get('h3').text()).toBe('Verify Telegram bot')
    expect(wrapper.text()).toContain('Check the saved bot token.')
    expect(wrapper.text()).not.toContain('Phone number')
    expect(wrapper.text()).not.toContain('QR code')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('start')).toEqual([['phone']])

    await wrapper.setProps({
      state: {
        credential_ref: 'cred_bot',
        provider_key: 'telegram',
        status: 'disconnected',
      },
    })
    expect(wrapper.get('h3').text()).toBe('Telegram bot authorization saved')
    expect(wrapper.text()).toContain('Attach this Account to a project')
    expect(wrapper.text()).not.toContain('Retry bot verification')
  })

  it('keeps a code entry locally when a submission has not changed the challenge', async () => {
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        state: {
          credential_ref: 'cred_native',
          provider_key: 'telegram',
          status: 'challenge',
          generation: 4,
          challenge: {
            generation: 4,
            kind: 'code',
            fields: ['code'],
            metadata: { timeout_seconds: 60 },
          },
        },
        mode: 'phone',
        allowsQr: true,
        busy: false,
      },
    })

    await wrapper.find<HTMLInputElement>('input[aria-label="Telegram code"]').setValue('12345')
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([[{ generation: 4, answer: { code: '12345' } }]])
    expect(wrapper.find<HTMLInputElement>('input[aria-label="Telegram code"]').element.value).toBe(
      '12345',
    )
  })

  it('uses challenge-specific phone, code, and two-step-password guidance', async () => {
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        state: {
          credential_ref: 'cred_native',
          provider_key: 'telegram',
          status: 'challenge',
          generation: 3,
          challenge: {
            generation: 3,
            kind: 'phone_number',
            fields: ['phone_number'],
            metadata: {},
          },
        },
        mode: 'phone',
        allowsQr: true,
        busy: false,
      },
    })

    expect(wrapper.get('h3').text()).toBe('Enter your phone number')
    expect(wrapper.text()).toContain('international format')
    expect(
      wrapper.find<HTMLInputElement>('input[aria-label="Phone number"]').attributes('placeholder'),
    ).toBe('+15551234567')
    expect(wrapper.get('label').attributes('for')).toBe(
      wrapper.get<HTMLInputElement>('input[aria-label="Phone number"]').attributes('id'),
    )
    expect(wrapper.get('button[type="submit"]').text()).toContain('Send Telegram code')

    await wrapper.setProps({
      state: {
        credential_ref: 'cred_native',
        provider_key: 'telegram',
        status: 'challenge',
        generation: 4,
        challenge: {
          generation: 4,
          kind: 'code',
          fields: ['code'],
          metadata: {
            delivery_type: 'authenticationCodeTypeTelegramMessage',
            timeout_seconds: 60,
          },
        },
      },
    })
    expect(wrapper.get('h3').text()).toBe('Enter the Telegram code')
    expect(wrapper.text()).toContain('Telegram message')
    expect(wrapper.text()).not.toContain('within 60 seconds')
    expect(wrapper.get('button[type="submit"]').text()).toContain('Verify code')

    await wrapper.setProps({
      state: {
        credential_ref: 'cred_native',
        provider_key: 'telegram',
        status: 'challenge',
        generation: 5,
        challenge: {
          generation: 5,
          kind: 'password',
          fields: ['password'],
          metadata: {},
        },
      },
    })
    expect(wrapper.get('h3').text()).toBe('Enter your two-step verification password')
    expect(wrapper.text()).toContain('not the Telegram code')
    expect(wrapper.get('button[type="submit"]').text()).toContain('Sign in')
  })

  it('offers phone or QR setup and only exposes a safe Telegram deep link for a QR state', async () => {
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        state: null,
        mode: 'phone',
        allowsQr: true,
        busy: false,
      },
    })
    expect(wrapper.text()).toContain('Phone number')
    expect(wrapper.text()).toContain('QR code')
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('start')).toEqual([['phone']])

    await wrapper.setProps({
      state: {
        credential_ref: 'cred_native',
        provider_key: 'telegram',
        status: 'challenge',
        generation: 5,
        challenge: {
          generation: 5,
          kind: 'qr',
          fields: [],
          qr_link: 'tg://login?token=transient',
        },
      },
    })
    const link = wrapper.find<HTMLAnchorElement>('a[href^="tg://login?"]')
    expect(link.exists()).toBe(true)
    expect(link.text()).toContain('Open Telegram')
    await wrapper.find('button[aria-label="Cancel authorization"]').trigger('click')
    expect(wrapper.emitted('cancel')).toEqual([[5]])
  })

  it('does not start authorization until edited Account changes are saved or discarded', async () => {
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        state: null,
        mode: 'phone',
        allowsQr: true,
        busy: false,
        dirty: true,
      },
    })

    const start = wrapper.get('button')
    expect(start.attributes('disabled')).toBeDefined()
    await start.trigger('click')
    expect(wrapper.emitted('start')).toBeUndefined()

    await wrapper.setProps({ dirty: false })
    await start.trigger('click')
    expect(wrapper.emitted('start')).toEqual([['phone']])
  })

  it('does not submit a pending Telegram challenge while edited Account changes are unsaved', async () => {
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        state: {
          credential_ref: 'cred_native',
          provider_key: 'telegram',
          status: 'challenge',
          generation: 8,
          challenge: {
            generation: 8,
            kind: 'code',
            fields: ['code'],
            metadata: {},
          },
        },
        mode: 'phone',
        allowsQr: true,
        busy: false,
        dirty: true,
      },
    })

    await wrapper.find<HTMLInputElement>('input[aria-label="Telegram code"]').setValue('12345')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    const cancel = wrapper.get('button[aria-label="Cancel authorization"]')
    expect(cancel.attributes('disabled')).toBeUndefined()
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('submit')).toBeUndefined()
    await cancel.trigger('click')
    expect(wrapper.emitted('cancel')).toEqual([[8]])

    await wrapper.setProps({ dirty: false })
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('submit')).toBeUndefined()
    await wrapper.find<HTMLInputElement>('input[aria-label="Telegram code"]').setValue('12345')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('submit')).toEqual([[{ generation: 8, answer: { code: '12345' } }]])
  })

  it('allows a local status refresh while TDLib starts or verifies an Account', async () => {
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        state: {
          credential_ref: 'cred_native',
          provider_key: 'telegram',
          status: 'starting',
          generation: 6,
          challenge: null,
        },
        mode: 'phone',
        allowsQr: true,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('starting this Account’s native session')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('refresh')).toEqual([[]])

    await wrapper.setProps({
      state: {
        credential_ref: 'cred_native',
        provider_key: 'telegram',
        status: 'verifying',
        generation: 6,
        challenge: null,
      },
    })
    expect(wrapper.text()).toContain("verifying this Account's native identity")
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('refresh')).toEqual([[], []])
  })

  it('clears the old answer when TDLib advances to a new generation or challenge kind', async () => {
    const state = (generation: number, kind: string, fields: string[]) => ({
      credential_ref: 'cred_native',
      provider_key: 'telegram',
      status: 'challenge',
      generation,
      challenge: { generation, kind, fields, metadata: {} },
    })
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        state: state(1, 'code', ['code']),
        mode: 'phone',
        allowsQr: true,
        busy: false,
      },
    })

    await wrapper.get<HTMLInputElement>('input[aria-label="Telegram code"]').setValue('11111')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()

    await wrapper.setProps({ state: state(2, 'code', ['code']) })
    expect(wrapper.get<HTMLInputElement>('input[aria-label="Telegram code"]').element.value).toBe(
      '',
    )
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()

    await wrapper.get<HTMLInputElement>('input[aria-label="Telegram code"]').setValue('22222')
    await wrapper.setProps({ state: state(2, 'password', ['password']) })
    expect(wrapper.get<HTMLInputElement>('input[aria-label="Password"]').element.value).toBe('')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()

    await wrapper
      .get<HTMLInputElement>('input[aria-label="Password"]')
      .setValue('two-step-password')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('submit')).toEqual([
      [{ generation: 2, answer: { password: 'two-step-password' } }],
    ])
  })

  it('clears an answer when a different Account has the same challenge generation', async () => {
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        state: {
          credential_ref: 'cred_one',
          provider_key: 'telegram',
          status: 'challenge',
          generation: 1,
          challenge: { generation: 1, kind: 'code', fields: ['code'] },
        },
        mode: 'phone',
        allowsQr: true,
        busy: false,
      },
    })

    await wrapper.get<HTMLInputElement>('input[aria-label="Telegram code"]').setValue('11111')
    await wrapper.setProps({
      state: {
        credential_ref: 'cred_two',
        provider_key: 'telegram',
        status: 'challenge',
        generation: 1,
        challenge: { generation: 1, kind: 'code', fields: ['code'] },
      },
    })
    expect(wrapper.get<HTMLInputElement>('input[aria-label="Telegram code"]').element.value).toBe(
      '',
    )
  })

  it('clears a secret answer after cancel even if a challenge returns with the same generation', async () => {
    const challengeState = {
      credential_ref: 'cred_native',
      provider_key: 'telegram',
      status: 'challenge',
      generation: 1,
      challenge: { generation: 1, kind: 'password', fields: ['password'] },
    }
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: { state: challengeState, mode: 'phone', allowsQr: true, busy: false },
    })

    await wrapper.get<HTMLInputElement>('input[aria-label="Password"]').setValue('old-secret')
    await wrapper.get('button[aria-label="Cancel authorization"]').trigger('click')
    expect(wrapper.emitted('cancel')).toEqual([[1]])
    await wrapper.setProps({
      state: { ...challengeState, status: 'disconnected', challenge: null },
    })
    await wrapper.setProps({ state: challengeState })
    expect(wrapper.get<HTMLInputElement>('input[aria-label="Password"]').element.value).toBe('')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('treats Telegram registration last name as optional and keeps email verification distinct', async () => {
    const wrapper = mount(NativeAccountAuthorizationPanel, {
      props: {
        state: {
          credential_ref: 'cred_native',
          provider_key: 'telegram',
          status: 'challenge',
          generation: 4,
          challenge: {
            generation: 4,
            kind: 'registration',
            fields: ['first_name', 'last_name'],
          },
        },
        mode: 'phone',
        allowsQr: true,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('Last name (optional)')
    await wrapper.get<HTMLInputElement>('input[aria-label="First name"]').setValue('Ada')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('submit')).toEqual([[{ generation: 4, answer: { first_name: 'Ada' } }]])

    await wrapper.setProps({
      state: {
        credential_ref: 'cred_native',
        provider_key: 'telegram',
        status: 'challenge',
        generation: 5,
        challenge: { generation: 5, kind: 'email_code', fields: ['code'] },
      },
    })
    expect(wrapper.get('h3').text()).toBe('Enter the email code')
    expect(wrapper.find('input[aria-label="Email code"]').exists()).toBe(true)

    await wrapper.setProps({
      state: {
        credential_ref: 'cred_native',
        provider_key: 'telegram',
        status: 'challenge',
        generation: 6,
        challenge: { generation: 6, kind: 'email_address', fields: ['email_address'] },
      },
    })
    expect(wrapper.get('h3').text()).toBe('Enter your email address')
    expect(
      wrapper.get<HTMLInputElement>('input[aria-label="Email address"]').attributes('type'),
    ).toBe('email')
  })

  it('separates a saved user sign-in from the explicit project session decision', async () => {
    const state = {
      credential_ref: 'cred_native',
      provider_key: 'telegram',
      status: 'disconnected',
      generation: 7,
      challenge: null,
    }
    const globalAccounts = mount(NativeAccountAuthorizationPanel, {
      props: { state, mode: 'phone', allowsQr: true, busy: false },
    })
    expect(globalAccounts.get('h3').text()).toBe('Telegram sign-in saved')
    expect(globalAccounts.text()).toContain('Attach this Account to a project')
    expect(globalAccounts.text()).not.toContain('ready to test')

    const projectConnections = mount(NativeAccountAuthorizationPanel, {
      props: { state, mode: 'phone', allowsQr: true, busy: false, sessionAvailable: true },
    })
    expect(projectConnections.text()).toContain('Connect the session below')

    await projectConnections.setProps({ state: { ...state, status: 'connected' } })
    expect(projectConnections.text()).toContain('This Account is connected')
    expect(projectConnections.text()).not.toContain('Connect the session below')
  })
})
