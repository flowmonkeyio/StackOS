import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { AuthField } from '@/views/connections/types'
import TelegramProxySetupSection from './TelegramProxySetupSection.vue'

const fields: AuthField[] = [
  { key: 'proxy_enabled', label: 'Use a proxy', type: 'boolean', secret: false, required: false },
  {
    key: 'proxy_type',
    label: 'Proxy type',
    type: 'select',
    secret: false,
    required: false,
    options: [
      { value: 'socks5', label: 'SOCKS5' },
      { value: 'http', label: 'HTTP' },
      { value: 'mtproto', label: 'MTProto' },
    ],
  },
  { key: 'proxy_host', label: 'Proxy host', type: 'text', secret: false, required: false },
  { key: 'proxy_port', label: 'Proxy port', type: 'number', secret: false, required: false },
  { key: 'proxy_http_only', label: 'HTTP requests only', type: 'boolean', secret: false, required: false },
  { key: 'proxy_username', label: 'Proxy username', type: 'text', secret: true, required: false },
  { key: 'proxy_password', label: 'Proxy password', type: 'text', secret: true, required: false },
  { key: 'proxy_secret', label: 'MTProto proxy secret', type: 'text', secret: true, required: false },
]

function mountSection(fieldValues: Record<string, string> = {}, fieldErrors: Record<string, string> = {}) {
  return mount(TelegramProxySetupSection, {
    props: {
      fields,
      fieldValues,
      fieldErrors,
      editing: false,
      secretPresent: {},
      inputType: (field: AuthField) => (field.type === 'number' ? 'number' as const : 'text' as const),
      isSecretField: (field: AuthField) => Boolean(field.secret),
      hasFieldOptions: (field: AuthField) => field.type === 'select',
      fieldOptions: (field: AuthField) =>
        (field.options ?? []).map((option) => ({
          value: String(option.value),
          label: String(option.label),
        })),
    },
  })
}

function updates(wrapper: ReturnType<typeof mountSection>) {
  return (wrapper.emitted('update:field') ?? []).map(([value]) => value)
}

describe('TelegramProxySetupSection', () => {
  it('starts as a compact direct-connection disclosure and reveals core fields only when enabled', async () => {
    const wrapper = mountSection()
    const disclosure = wrapper.get('button[aria-expanded]')
    expect(disclosure.text()).toContain('Proxy settings (optional)')
    expect(disclosure.text()).toContain('Direct connection')
    expect(disclosure.attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)

    await disclosure.trigger('click')
    expect(disclosure.attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('label[for="connection-field-proxy_enabled"]').text()).toContain('Use a proxy')
    expect(wrapper.find('#connection-field-proxy_type').exists()).toBe(false)

    await wrapper.get<HTMLInputElement>('#connection-field-proxy_enabled').setValue(true)
    expect(updates(wrapper)).toContainEqual({ fieldKey: 'proxy_enabled', value: 'true' })
    await wrapper.setProps({ fieldValues: { proxy_enabled: 'true' } })

    expect(wrapper.find('#connection-field-proxy_type').exists()).toBe(true)
    expect(wrapper.find('#connection-field-proxy_host').exists()).toBe(true)
    expect(wrapper.find('#connection-field-proxy_port').exists()).toBe(true)
    expect(wrapper.find('#connection-field-proxy_username').exists()).toBe(false)
    expect(wrapper.find('#connection-field-proxy_secret').exists()).toBe(false)
  })

  it('shows only the selected protocol’s relevant extra fields', async () => {
    const wrapper = mountSection({ proxy_enabled: 'true', proxy_type: 'http' })
    expect(wrapper.get('button[aria-expanded]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('#connection-field-proxy_username').exists()).toBe(false)
    expect(wrapper.find('#connection-field-proxy_http_only').exists()).toBe(false)
    await wrapper.get('button[aria-label="Show advanced proxy options"]').trigger('click')
    expect(wrapper.find('#connection-field-proxy_username').exists()).toBe(true)
    expect(wrapper.find('#connection-field-proxy_password').exists()).toBe(true)
    expect(wrapper.find('#connection-field-proxy_http_only').exists()).toBe(true)
    expect(wrapper.find('#connection-field-proxy_secret').exists()).toBe(false)

    await wrapper.setProps({ fieldValues: { proxy_enabled: 'true', proxy_type: 'mtproto' } })
    expect(wrapper.find('#connection-field-proxy_secret').exists()).toBe(true)
    expect(wrapper.find('#connection-field-proxy_username').exists()).toBe(false)
    expect(wrapper.find('#connection-field-proxy_http_only').exists()).toBe(false)
  })

  it('clears incompatible draft fields when protocol changes or proxy is disabled', async () => {
    const wrapper = mountSection({
      proxy_enabled: 'true',
      proxy_type: 'http',
      proxy_host: 'proxy.example.com',
      proxy_port: '8080',
      proxy_http_only: 'true',
      proxy_username: 'user',
      proxy_password: 'password',
    })

    await wrapper.get('button[aria-label="Show advanced proxy options"]').trigger('click')
    // UiSelect is custom; the child field emits its normalized selection.
    const proxyType = wrapper.findAllComponents({ name: 'ConnectionCredentialField' })
      .find((component) => component.props('field').key === 'proxy_type')
    expect(proxyType).toBeDefined()
    proxyType!.vm.$emit('update:modelValue', 'mtproto')
    await wrapper.vm.$nextTick()
    expect(updates(wrapper)).toEqual(expect.arrayContaining([
      { fieldKey: 'proxy_type', value: 'mtproto' },
      { fieldKey: 'proxy_http_only', value: '' },
      { fieldKey: 'proxy_username', value: '' },
      { fieldKey: 'proxy_password', value: '' },
    ]))

    await wrapper.setProps({
      fieldValues: {
        proxy_enabled: 'true',
        proxy_type: 'mtproto',
        proxy_host: 'proxy.example.com',
        proxy_port: '8080',
        proxy_secret: 'abcdef',
      },
    })
    const mtprotoType = wrapper.findAllComponents({ name: 'ConnectionCredentialField' })
      .find((component) => component.props('field').key === 'proxy_type')
    mtprotoType!.vm.$emit('update:modelValue', 'socks5')
    await wrapper.vm.$nextTick()
    expect(updates(wrapper)).toContainEqual({ fieldKey: 'proxy_secret', value: '' })

    await wrapper.get<HTMLInputElement>('#connection-field-proxy_enabled').setValue(false)
    expect(updates(wrapper)).toEqual(expect.arrayContaining([
      { fieldKey: 'proxy_enabled', value: 'false' },
      { fieldKey: 'proxy_type', value: '' },
      { fieldKey: 'proxy_host', value: '' },
      { fieldKey: 'proxy_port', value: '' },
      { fieldKey: 'proxy_secret', value: '' },
    ]))
  })

  it('clears HTTP-only to false for SOCKS5 and reveals protocol errors for repair', async () => {
    const wrapper = mountSection({
      proxy_enabled: 'true',
      proxy_type: 'http',
      proxy_http_only: 'true',
    })
    const proxyType = wrapper.findAllComponents({ name: 'ConnectionCredentialField' })
      .find((component) => component.props('field').key === 'proxy_type')
    proxyType!.vm.$emit('update:modelValue', 'socks5')
    await wrapper.vm.$nextTick()
    expect(updates(wrapper)).toContainEqual({ fieldKey: 'proxy_http_only', value: 'false' })

    await wrapper.setProps({
      fieldValues: { proxy_enabled: 'true', proxy_type: 'socks5' },
      fieldErrors: { proxy_username: 'Check this value.' },
    })
    expect(wrapper.get('button[aria-label="Hide advanced proxy options"]').attributes('aria-expanded'))
      .toBe('true')
    expect(wrapper.get('#connection-field-proxy_username').attributes('aria-invalid')).toBe('true')
  })

  it('opens for a relevant error without a watcher but ignores errors for hidden proxy fields', () => {
    const enabledError = mountSection({}, { proxy_enabled: 'Review this setting.' })
    expect(enabledError.get('button[aria-expanded]').attributes('aria-expanded')).toBe('true')
    expect(enabledError.get('[role="alert"]').text()).toContain('Review this setting.')

    const hiddenError = mountSection({}, { proxy_host: 'Old proxy error.' })
    expect(hiddenError.get('button[aria-expanded]').attributes('aria-expanded')).toBe('false')
  })
})
