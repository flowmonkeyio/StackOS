import { ref } from 'vue'

import type { SchemaAuthProviderOut } from '@/api'
import type { AuthField, AuthMethod } from '@/views/connections/types'

export function useConnectionForm() {
  const selectedProviderKey = ref('')
  const selectedMethodByProvider = ref<Record<string, string>>({})
  const displayNameByForm = ref<Record<string, string>>({})
  const fieldsByForm = ref<Record<string, Record<string, string>>>({})

  function authMethods(provider: SchemaAuthProviderOut): AuthMethod[] {
    return provider.auth_methods ?? []
  }

  function selectedMethodKey(provider: SchemaAuthProviderOut): string {
    const methods = authMethods(provider)
    const selectedKey = selectedMethodByProvider.value[provider.key]
    if (selectedKey && methods.some((method) => method.key === selectedKey)) return selectedKey
    return methods.length === 1 ? methods[0]?.key ?? '' : ''
  }

  function selectedMethod(provider: SchemaAuthProviderOut): AuthMethod | null {
    const key = selectedMethodKey(provider)
    return authMethods(provider).find((method) => method.key === key) ?? null
  }

  function setSelectedMethod(providerKey: string, value: string | number | null): void {
    selectedMethodByProvider.value = {
      ...selectedMethodByProvider.value,
      [providerKey]: String(value ?? ''),
    }
  }

  function formKey(providerKey: string, methodKey: string): string {
    return `${providerKey}:${methodKey}`
  }

  function supportsCredential(provider: SchemaAuthProviderOut): boolean {
    return authMethods(provider).some(
      (method) =>
        method.payload_format !== 'none' || (method.fields ?? []).length > 0 || method.interactive,
    )
  }

  function canAddProvider(provider: SchemaAuthProviderOut): boolean {
    return provider.config_json?.connection_setup !== 'project-local-plugin-required'
  }

  function inputType(field: AuthField): 'text' | 'url' | 'number' | 'email' {
    if (field.type === 'url') return 'url'
    if (field.type === 'number') return 'number'
    if (field.type === 'email') return 'email'
    return 'text'
  }

  function isSecretField(field: AuthField): boolean {
    return field.secret || ['secret', 'password'].includes(field.type)
  }

  function methodFields(method: AuthMethod | null | undefined): AuthField[] {
    return method?.fields ?? []
  }

  function fieldOptions(field: AuthField): Array<{ value: string; label: string }> {
    return (field.options ?? [])
      .map((option) => {
        const value = option.value ?? option.key ?? option.label
        const label = option.label ?? option.value ?? option.key
        return value && label ? { value: String(value), label: String(label) } : null
      })
      .filter((option): option is { value: string; label: string } => option !== null)
  }

  function hasFieldOptions(field: AuthField): boolean {
    return field.type === 'select' || fieldOptions(field).length > 0
  }

  function fieldValue(providerKey: string, methodKey: string, fieldKey: string): string {
    return fieldsByForm.value[formKey(providerKey, methodKey)]?.[fieldKey] ?? ''
  }

  function setFieldValue(
    providerKey: string,
    methodKey: string,
    fieldKey: string,
    value: string | number | null,
  ): void {
    const key = formKey(providerKey, methodKey)
    fieldsByForm.value = {
      ...fieldsByForm.value,
      [key]: {
        ...(fieldsByForm.value[key] ?? {}),
        [fieldKey]: value === null ? '' : String(value),
      },
    }
  }

  function displayNameValue(providerKey: string, methodKey: string): string {
    return displayNameByForm.value[formKey(providerKey, methodKey)] ?? ''
  }

  function setDisplayNameValue(
    providerKey: string,
    methodKey: string,
    value: string | number | null,
  ) {
    displayNameByForm.value = {
      ...displayNameByForm.value,
      [formKey(providerKey, methodKey)]: String(value ?? ''),
    }
  }

  function setSelectedProvider(value: string | number | null): void {
    selectedProviderKey.value = String(value ?? '')
  }

  function clearForm(providerKey: string, methodKey: string): void {
    const key = formKey(providerKey, methodKey)
    fieldsByForm.value = { ...fieldsByForm.value, [key]: {} }
    displayNameByForm.value = { ...displayNameByForm.value, [key]: '' }
  }

  function clearProviderForms(providerKey: string): void {
    const prefix = `${providerKey}:`
    fieldsByForm.value = Object.fromEntries(
      Object.entries(fieldsByForm.value).filter(([key]) => !key.startsWith(prefix)),
    )
    displayNameByForm.value = Object.fromEntries(
      Object.entries(displayNameByForm.value).filter(([key]) => !key.startsWith(prefix)),
    )
  }

  function populateForm(
    providerKey: string,
    methodKey: string,
    values: Record<string, string>,
    displayName: string,
  ): void {
    const key = formKey(providerKey, methodKey)
    fieldsByForm.value = { ...fieldsByForm.value, [key]: { ...values } }
    displayNameByForm.value = { ...displayNameByForm.value, [key]: displayName }
  }

  return {
    selectedProviderKey,
    selectedMethodByProvider,
    displayNameByForm,
    fieldsByForm,
    authMethods,
    selectedMethodKey,
    selectedMethod,
    setSelectedMethod,
    formKey,
    supportsCredential,
    canAddProvider,
    inputType,
    isSecretField,
    methodFields,
    fieldOptions,
    hasFieldOptions,
    fieldValue,
    setFieldValue,
    displayNameValue,
    setDisplayNameValue,
    setSelectedProvider,
    clearForm,
    clearProviderForms,
    populateForm,
  }
}
