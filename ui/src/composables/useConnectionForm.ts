import { ref } from 'vue'

import type { SchemaAuthProviderOut } from '@/api'
import type { AuthField, AuthMethod } from '@/views/connections/types'

export function useConnectionForm() {
  const selectedProviderKey = ref('')
  const selectedMethodByProvider = ref<Record<string, string>>({})
  const labelByForm = ref<Record<string, string>>({})
  const profileByForm = ref<Record<string, string>>({})
  const fieldsByForm = ref<Record<string, Record<string, string>>>({})

  function authMethods(provider: SchemaAuthProviderOut): AuthMethod[] {
    return provider.auth_methods ?? []
  }

  function selectedMethodKey(provider: SchemaAuthProviderOut): string {
    return selectedMethodByProvider.value[provider.key] ?? authMethods(provider)[0]?.key ?? ''
  }

  function selectedMethod(provider: SchemaAuthProviderOut): AuthMethod | null {
    const key = selectedMethodKey(provider)
    return (
      authMethods(provider).find((method) => method.key === key) ?? authMethods(provider)[0] ?? null
    )
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

  function profileValue(providerKey: string, methodKey: string): string {
    return profileByForm.value[formKey(providerKey, methodKey)] ?? ''
  }

  function setProfileValue(providerKey: string, methodKey: string, value: string | number | null) {
    profileByForm.value = {
      ...profileByForm.value,
      [formKey(providerKey, methodKey)]: String(value ?? ''),
    }
  }

  function labelValue(providerKey: string, methodKey: string): string {
    return labelByForm.value[formKey(providerKey, methodKey)] ?? ''
  }

  function setLabelValue(providerKey: string, methodKey: string, value: string | number | null) {
    labelByForm.value = {
      ...labelByForm.value,
      [formKey(providerKey, methodKey)]: String(value ?? ''),
    }
  }

  function setSelectedProvider(value: string | number | null): void {
    selectedProviderKey.value = String(value ?? '')
  }

  function clearForm(providerKey: string, methodKey: string): void {
    const key = formKey(providerKey, methodKey)
    fieldsByForm.value = { ...fieldsByForm.value, [key]: {} }
    profileByForm.value = { ...profileByForm.value, [key]: '' }
    labelByForm.value = { ...labelByForm.value, [key]: '' }
  }

  function populateForm(
    providerKey: string,
    methodKey: string,
    values: Record<string, string>,
    profile: string,
    label: string,
  ): void {
    const key = formKey(providerKey, methodKey)
    fieldsByForm.value = { ...fieldsByForm.value, [key]: { ...values } }
    profileByForm.value = { ...profileByForm.value, [key]: profile }
    labelByForm.value = { ...labelByForm.value, [key]: label }
  }

  return {
    selectedProviderKey,
    selectedMethodByProvider,
    labelByForm,
    profileByForm,
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
    profileValue,
    setProfileValue,
    labelValue,
    setLabelValue,
    setSelectedProvider,
    clearForm,
    populateForm,
  }
}
