import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ConnectionCredentialField from './ConnectionCredentialField.vue'

describe('ConnectionCredentialField', () => {
  it.each(['api_key', 'password', 'signing_secret'])(
    'shows a saved-secret placeholder for %s without populating the input',
    async (fieldKey) => {
      const wrapper = mount(ConnectionCredentialField, {
        props: {
          field: {
            key: fieldKey,
            label: 'Secret value',
            type: 'secret',
            secret: true,
            required: true,
            placeholder: 'provider-specific-placeholder',
            description: 'Provider credential.',
            options: null,
          },
          modelValue: '',
          inputType: 'text',
          secret: true,
          select: false,
          options: [],
          editing: true,
          secretPresent: true,
        },
      })

      const input = wrapper.get<HTMLInputElement>('input')
      expect(input.attributes('placeholder')).toBe('••••••••')
      expect(input.attributes('required')).toBeUndefined()
      expect(input.element.value).toBe('')
      expect(wrapper.get('label').text()).toBe('Secret value')
      expect(wrapper.get('.ui-form-field__label-row').text()).toContain('saved')
      expect(wrapper.text()).toContain('Saved — leave blank to keep it.')
      expect(wrapper.html()).not.toContain('provider-secret')

      await wrapper.setProps({ modelValue: 'replacement' })
      expect(wrapper.get('.ui-form-field__label-row').text()).not.toContain('saved')
      expect(wrapper.get('label').text()).toBe('Secret value')
    },
  )

  it('uses the provider placeholder when no saved secret exists', () => {
    const wrapper = mount(ConnectionCredentialField, {
      props: {
        field: {
          key: 'api_key',
          label: 'API key',
          type: 'secret',
          secret: true,
          required: true,
          placeholder: 'api-...',
          description: 'Create a key in the provider console.',
          options: null,
        },
        modelValue: '',
        inputType: 'text',
        secret: true,
        select: false,
        options: [],
        editing: true,
        secretPresent: false,
      },
    })

    const input = wrapper.get('input')
    expect(input.attributes('placeholder')).toBe('api-...')
    expect(input.attributes('required')).toBeDefined()
    expect(wrapper.get('label').text()).toContain('*')
    expect(wrapper.text()).not.toContain('Saved — leave blank to keep it.')
  })
})
