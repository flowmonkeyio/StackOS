import { computed, nextTick, ref, type ComputedRef } from 'vue'
import { storeToRefs } from 'pinia'

import type { SchemaAccountOut, SchemaAuthProviderOut } from '@/api'
import { useConnectionForm } from '@/composables/useConnectionForm'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { useStackOsCatalogStore } from '@/stores/plugins'
import {
  connectionActionKey,
  credentialTestMessage,
  providerActionKey,
  providerGroupLabel,
} from '@/views/connections/formatters'
import { connectionFieldInputId } from '@/views/connections/fieldIds'
import type {
  AuthMethod,
  MessageMap,
  MessageTone,
  OAuthReturnStatus,
} from '@/views/connections/types'

export type AccountRow = Omit<SchemaAccountOut, 'project_ids'> & {
  id: string
  project_ids: number[]
}

export interface CommunicationAccountUse {
  project_id: number
  profile_ref: string
  profile_key: string
  profile_display_name: string
  provider_key: string
  credential_ref: string | null
  profile_enabled: boolean
  ingress_enabled: boolean
  owns_provider_ingress: boolean
  binding_state: string
  repair_message: string
  attention_required: boolean
  attention_message: string | null
  ingress_url: string | null
}

export function useAccountCredentials(attachProjectId: ComputedRef<number | null>) {
  const catalogStore = useStackOsCatalogStore()
  const { globalAccountsStatus, authProviders, loading, error } = storeToRefs(catalogStore)
  const {
    selectedProviderKey,
    authMethods,
    selectedMethodKey,
    selectedMethod,
    setSelectedMethod: setRawSelectedMethod,
    supportsCredential,
    canAddProvider,
    inputType,
    isSecretField,
    methodFields,
    fieldOptions,
    hasFieldOptions,
    fieldValue,
    setFieldValue: setRawFieldValue,
    displayNameValue,
    setDisplayNameValue: setRawDisplayNameValue,
    setSelectedProvider: setRawSelectedProvider,
    clearForm,
    clearProviderForms,
    populateForm,
  } = useConnectionForm()

  const panelOpen = ref(false)
  const busyAction = ref<string | null>(null)
  const providerMessages = ref<MessageMap>({})
  const accountMessages = ref<MessageMap>({})
  const oauthReturnMessage = ref<{ tone: MessageTone; text: string } | null>(null)
  const fieldErrors = ref<Record<string, string>>({})
  const pendingRevoke = ref<AccountRow | null>(null)
  const editingCredentialRef = ref<string | null>(null)
  const editingSecretPresent = ref<Record<string, boolean>>({})
  const communicationAccountUses = ref<CommunicationAccountUse[]>([])
  const editing = computed(() => editingCredentialRef.value !== null)

  const accounts = computed<AccountRow[]>(() =>
    (globalAccountsStatus.value?.accounts ?? []).map((account) => ({
      ...account,
      id: account.credential_ref,
      project_ids: account.project_ids ?? [],
    })),
  )
  const visibleAuthProviders = computed(() => (authProviders.value ?? []).filter(canAddProvider))
  const providerByKey = computed(() => {
    const rows = new Map<string, SchemaAuthProviderOut>()
    for (const provider of visibleAuthProviders.value) rows.set(provider.key, provider)
    return rows
  })
  const providerOptions = computed(() =>
    visibleAuthProviders.value.map((provider) => ({
      value: provider.key,
      label: provider.name,
      group: providerGroupLabel(provider),
    })),
  )
  const selectedProvider = computed(() => {
    if (selectedProviderKey.value) {
      const provider = providerByKey.value.get(selectedProviderKey.value)
      if (provider) return provider
    }
    return visibleAuthProviders.value[0] ?? null
  })

  async function load(): Promise<void> {
    const [, usage] = await Promise.all([
      catalogStore.refreshAccounts(),
      callOperation<{ uses: CommunicationAccountUse[] }>('communicationProfile.accountUsage', {}),
    ])
    communicationAccountUses.value = usage.uses ?? []
  }

  function ensureSelectableProvider(): void {
    const providers = visibleAuthProviders.value
    if (!providers.length) {
      selectedProviderKey.value = ''
      return
    }
    if (!providerByKey.value.has(selectedProviderKey.value)) {
      selectedProviderKey.value = providers[0]?.key ?? ''
    }
  }

  function seedDisplayName(provider: SchemaAuthProviderOut): void {
    const method = selectedMethod(provider)
    if (!method || displayNameValue(provider.key, method.key).trim()) return
    const existingNames = new Set(
      accounts.value
        .filter((account) => account.provider_key === provider.key)
        .map((account) => account.display_name.toLocaleLowerCase()),
    )
    const defaultName = `${provider.name} - Default`
    if (!existingNames.has(defaultName.toLocaleLowerCase())) {
      setDisplayNameValue(provider.key, method.key, defaultName)
      return
    }
    let suffix = 2
    while (existingNames.has(`${provider.name} - ${suffix}`.toLocaleLowerCase())) suffix += 1
    setDisplayNameValue(provider.key, method.key, `${provider.name} - ${suffix}`)
  }

  function openAddAccount(providerKey?: string): void {
    editingCredentialRef.value = null
    editingSecretPresent.value = {}
    if (providerKey && providerByKey.value.has(providerKey)) {
      setRawSelectedProvider(providerKey)
    }
    ensureSelectableProvider()
    const provider = selectedProvider.value
    if (provider) {
      clearProviderForms(provider.key)
      if (authMethods(provider).length > 1) {
        setRawSelectedMethod(provider.key, '')
      } else {
        seedDisplayName(provider)
      }
    }
    fieldErrors.value = {}
    panelOpen.value = true
  }

  async function openEditAccount(account: AccountRow): Promise<void> {
    busyAction.value = connectionActionKey(account.credential_ref, 'edit')
    try {
      const state = await catalogStore.getCredential(account.credential_ref)
      const provider = providerByKey.value.get(state.account.provider_key)
      if (!provider) throw new Error('The provider is no longer available.')
      const method = authMethods(provider).find(
        (candidate) => candidate.key === state.account.auth_method_key,
      )
      if (!method) throw new Error('The saved authentication method is no longer available.')
      setRawSelectedProvider(provider.key)
      setRawSelectedMethod(provider.key, method.key)
      const values: Record<string, string> = {}
      for (const [key, value] of Object.entries(state.values)) {
        if (Array.isArray(value)) values[key] = value.map(String).join(',')
        else if (value !== null && ['string', 'number', 'boolean'].includes(typeof value)) {
          values[key] = String(value)
        }
      }
      populateForm(provider.key, method.key, values, state.account.display_name)
      editingCredentialRef.value = account.credential_ref
      editingSecretPresent.value = state.secret_present
      fieldErrors.value = {}
      panelOpen.value = true
    } catch (err) {
      setAccountMessage(
        account.credential_ref,
        'danger',
        formatApiError(err, 'failed to load Account settings'),
      )
    } finally {
      busyAction.value = null
    }
  }

  function selectProvider(value: string | number | null): void {
    fieldErrors.value = {}
    setRawSelectedProvider(value)
    const provider = providerByKey.value.get(String(value ?? ''))
    if (!provider) return
    clearProviderForms(provider.key)
    if (authMethods(provider).length > 1) setRawSelectedMethod(provider.key, '')
    else seedDisplayName(provider)
  }

  function setSelectedMethod(providerKey: string, value: string | number | null): void {
    const provider = providerByKey.value.get(providerKey)
    const methodKey = String(value ?? '')
    if (!provider || !authMethods(provider).some((method) => method.key === methodKey)) return
    clearProviderForms(providerKey)
    setRawSelectedMethod(providerKey, methodKey)
    seedDisplayName(provider)
  }

  function setFieldValue(
    providerKey: string,
    methodKey: string,
    fieldKey: string,
    value: string | number | null,
  ): void {
    if (fieldErrors.value[fieldKey]) {
      const next = { ...fieldErrors.value }
      delete next[fieldKey]
      fieldErrors.value = next
    }
    setRawFieldValue(providerKey, methodKey, fieldKey, value)
  }

  function setDisplayNameValue(
    providerKey: string,
    methodKey: string,
    value: string | number | null,
  ): void {
    if (fieldErrors.value.display_name) {
      const next = { ...fieldErrors.value }
      delete next.display_name
      fieldErrors.value = next
    }
    setRawDisplayNameValue(providerKey, methodKey, value)
  }

  function accountDraft(
    provider: SchemaAuthProviderOut,
    method: AuthMethod,
  ): { fields: Record<string, string>; displayName: string } | null {
    const fields: Record<string, string> = {}
    const errors: Record<string, string> = {}
    for (const field of method.fields ?? []) {
      const value = fieldValue(provider.key, method.key, field.key)
      const blank = value.trim() === ''
      const preservedSecret = editing.value && field.secret && editingSecretPresent.value[field.key]
      if (field.required && blank && !preservedSecret)
        errors[field.key] = `${field.label} is required.`
      if (editing.value && field.secret && blank) continue
      if (!editing.value && blank) continue
      fields[field.key] = value
    }
    const displayName = displayNameValue(provider.key, method.key).trim()
    if (!displayName) errors.display_name = 'Account name is required.'
    fieldErrors.value = errors
    const firstInvalidKey = Object.keys(errors)[0]
    if (firstInvalidKey) {
      setProviderMessage(provider.key, 'danger', 'Complete the required fields to continue.')
      void nextTick(() =>
        document
          .getElementById(
            firstInvalidKey === 'display_name'
              ? 'account-display-name'
              : connectionFieldInputId(firstInvalidKey),
          )
          ?.focus(),
      )
      return null
    }
    return { fields, displayName }
  }

  async function saveAccount(provider: SchemaAuthProviderOut): Promise<string | null> {
    const method = selectedMethod(provider)
    if (!method || method.payload_format === 'none') return null
    const draft = accountDraft(provider, method)
    if (!draft) return null
    busyAction.value = providerActionKey(provider.key, 'save')
    try {
      if (editingCredentialRef.value) {
        const credentialRef = editingCredentialRef.value
        await catalogStore.updateCredential(credentialRef, {
          display_name: draft.displayName,
          fields: draft.fields,
        })
        clearForm(provider.key, method.key)
        editingCredentialRef.value = null
        editingSecretPresent.value = {}
        setAccountMessage(credentialRef, 'success', 'Account updated.')
        panelOpen.value = false
        return credentialRef
      }
      const response = await catalogStore.storeCredential(provider.key, {
        auth_method_key: method.key,
        display_name: draft.displayName,
        fields: draft.fields,
        attach_project_id: attachProjectId.value,
      })
      const credentialRef = response.data.credential_ref
      clearForm(provider.key, method.key)
      if (attachProjectId.value) {
        await catalogStore.refreshAuth(attachProjectId.value, { silent: true })
      }
      try {
        const tested = await catalogStore.testCredential(credentialRef)
        setAccountMessage(
          credentialRef,
          tested.data.ok ? 'success' : 'danger',
          tested.data.ok
            ? credentialTestMessage(provider.key, tested.data.metadata, 'Account verified.')
            : `${tested.data.summary} The Account was saved; retry verification from Accounts.`,
        )
      } catch (testError) {
        setAccountMessage(
          credentialRef,
          'danger',
          `Account saved, but verification did not complete. ${formatApiError(testError, 'Retry from Accounts.')}`,
        )
      }
      panelOpen.value = false
      return credentialRef
    } catch (err) {
      setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to save Account'))
      return null
    } finally {
      busyAction.value = null
    }
  }

  async function startProvider(provider: SchemaAuthProviderOut): Promise<string | null> {
    const method = selectedMethod(provider)
    if (!method?.interactive) return null
    const draft = accountDraft(provider, method)
    if (!draft) return null
    busyAction.value = providerActionKey(provider.key, 'start')
    oauthReturnMessage.value = null
    try {
      let credentialRef = editingCredentialRef.value
      if (credentialRef) {
        await catalogStore.updateCredential(credentialRef, {
          display_name: draft.displayName,
          fields: draft.fields,
        })
      } else {
        const stored = await catalogStore.storeCredential(provider.key, {
          auth_method_key: method.key,
          display_name: draft.displayName,
          fields: draft.fields,
          attach_project_id: attachProjectId.value,
        })
        credentialRef = stored.data.credential_ref
      }
      const response = await catalogStore.startCredential(provider.key, {
        auth_method_key: method.key,
        credential_ref: credentialRef,
        attach_project_id: attachProjectId.value,
        return_surface: attachProjectId.value ? 'project-connections' : 'accounts',
      })
      const safeUrl = safeAuthorizationUrl(response.data.authorization_url)
      if (!safeUrl || response.data.credential_ref !== credentialRef) {
        setProviderMessage(provider.key, 'danger', 'The provider returned an invalid setup URL.')
        return null
      }
      clearForm(provider.key, method.key)
      editingCredentialRef.value = null
      editingSecretPresent.value = {}
      panelOpen.value = false
      return safeUrl
    } catch (err) {
      setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to start auth flow'))
      return null
    } finally {
      busyAction.value = null
    }
  }

  async function testAccount(account: AccountRow): Promise<void> {
    busyAction.value = connectionActionKey(account.credential_ref, 'test')
    try {
      const response = await catalogStore.testCredential(account.credential_ref)
      setAccountMessage(
        account.credential_ref,
        response.data.ok ? 'success' : 'danger',
        response.data.ok
          ? credentialTestMessage(
              response.data.provider_key,
              response.data.metadata,
              response.data.summary,
            )
          : response.data.summary,
      )
    } catch (err) {
      setAccountMessage(
        account.credential_ref,
        'danger',
        formatApiError(err, 'failed to test Account'),
      )
    } finally {
      busyAction.value = null
    }
  }

  function requestRevoke(account: AccountRow): void {
    pendingRevoke.value = account
  }

  async function confirmRevoke(): Promise<void> {
    const account = pendingRevoke.value
    if (!account) return
    busyAction.value = connectionActionKey(account.credential_ref, 'revoke')
    try {
      await catalogStore.revokeCredential(account.credential_ref)
    } catch (err) {
      setAccountMessage(
        account.credential_ref,
        'danger',
        formatApiError(err, 'failed to revoke Account'),
      )
    } finally {
      busyAction.value = null
      pendingRevoke.value = null
    }
  }

  function applyOAuthReturn(status: OAuthReturnStatus, providerKey: string | null): void {
    const name = providerKey ? (providerByKey.value.get(providerKey)?.name ?? providerKey) : 'OAuth'
    const messages: Record<OAuthReturnStatus, { tone: MessageTone; text: string }> = {
      connected: { tone: 'success', text: `${name} Account connected successfully.` },
      denied: { tone: 'warning', text: `${name} authorization was denied.` },
      expired: { tone: 'warning', text: `${name} authorization expired. Start setup again.` },
      'repair-required': { tone: 'danger', text: `${name} authorization needs repair.` },
      error: { tone: 'danger', text: `${name} authorization could not be completed.` },
    }
    oauthReturnMessage.value = messages[status]
    panelOpen.value = false
  }

  function setProviderMessage(providerKey: string, tone: MessageTone, text: string): void {
    providerMessages.value = { ...providerMessages.value, [providerKey]: { tone, text } }
  }

  function setAccountMessage(credentialRef: string, tone: MessageTone, text: string): void {
    accountMessages.value = { ...accountMessages.value, [credentialRef]: { tone, text } }
  }

  return {
    loading,
    error,
    panelOpen,
    busyAction,
    providerMessages,
    accountMessages,
    oauthReturnMessage,
    fieldErrors,
    pendingRevoke,
    editing,
    editingSecretPresent,
    authMethods,
    selectedMethodKey,
    selectedMethod,
    setSelectedMethod,
    supportsCredential,
    inputType,
    isSecretField,
    methodFields,
    fieldOptions,
    hasFieldOptions,
    fieldValue,
    setFieldValue,
    displayNameValue,
    setDisplayNameValue,
    setSelectedProvider: selectProvider,
    accounts,
    communicationAccountUses,
    visibleAuthProviders,
    providerOptions,
    selectedProvider,
    load,
    openAddAccount,
    openEditAccount,
    saveAccount,
    startProvider,
    testAccount,
    requestRevoke,
    confirmRevoke,
    applyOAuthReturn,
    connectionActionKey,
  }
}

function safeAuthorizationUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.username || url.password || url.hash) return null
    return url.href
  } catch {
    return null
  }
}
