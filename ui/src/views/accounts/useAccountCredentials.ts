import { computed, nextTick, ref, type ComputedRef } from 'vue'
import { storeToRefs } from 'pinia'

import type {
  SchemaAccountAuthStatusOut,
  SchemaAccountOut,
  SchemaAuthProviderOut,
  SchemaAuthStartRequest,
  SchemaAuthStartOut,
} from '@/api'
import { AuthStartRequestAuthorization_mode } from '@/api'
import { useConnectionForm } from '@/composables/useConnectionForm'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import {
  type TelegramAccountSessionStatus,
  useStackOsCatalogStore,
} from '@/stores/plugins'
import {
  connectionActionKey,
  credentialTestMessage,
  providerActionKey,
  providerGroupLabel,
} from '@/views/connections/formatters'
import { connectionFieldInputId } from '@/views/connections/fieldIds'
import { credentialVerificationMessage } from '@/views/connections/credentialPresentation'
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

type NativeAuthorizationMode = 'phone' | 'qr'

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
  const nativeAuthorization = ref<SchemaAccountAuthStatusOut | null>(null)
  const nativeAuthorizationCredentialRef = ref<string | null>(null)
  const nativeAuthorizationMode = ref<NativeAuthorizationMode>('phone')
  const nativeSession = ref<TelegramAccountSessionStatus | null>(null)
  const nativeAccountDraftDirty = ref(false)
  const telegramApplicationConfigured = ref<boolean | null>(null)
  const telegramApplicationBusy = ref(false)
  const telegramApplicationError = ref<string | null>(null)
  let telegramApplicationRequestId = 0
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
  const nativeAuthorizationEnabled = computed(
    () => isNativeAuthorization(selectedProvider.value ? selectedMethod(selectedProvider.value) : null),
  )
  const nativeAuthorizationAllowsQr = computed(
    () =>
      nativeAccountKind(selectedProvider.value ? selectedMethod(selectedProvider.value) : null) ===
      'user',
  )
  const nativeAuthorizationActive = computed(
    () => nativeAuthorizationEnabled.value && nativeAuthorizationCredentialRef.value !== null,
  )
  const nativeAuthorizationBusy = computed(
    () =>
      nativeAuthorizationCredentialRef.value !== null &&
      busyAction.value === connectionActionKey(nativeAuthorizationCredentialRef.value, 'authorize'),
  )
  const nativeSessionActive = computed(
    () =>
      isNativeAuthorization(selectedProvider.value ? selectedMethod(selectedProvider.value) : null) &&
      nativeAuthorizationCredentialRef.value !== null &&
      attachProjectId.value !== null,
  )
  const nativeSessionBusy = computed(
    () =>
      nativeAuthorizationCredentialRef.value !== null &&
      busyAction.value === connectionActionKey(nativeAuthorizationCredentialRef.value, 'session'),
  )

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
    nativeAccountDraftDirty.value = false
    clearNativeAuthorization()
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
    if (provider?.key === 'telegram') void refreshTelegramApplicationStatus()
  }

  async function openEditAccount(account: AccountRow): Promise<void> {
    nativeAccountDraftDirty.value = false
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
      if (isNativeAuthorization(method)) {
        nativeAuthorizationCredentialRef.value = account.credential_ref
        nativeAuthorization.value = {
          credential_ref: account.credential_ref,
          provider_key: provider.key,
          status: state.account.status,
          generation: null,
          challenge: null,
        }
        await refreshNativeAuthorization(account.credential_ref)
        await refreshNativeSession(account.credential_ref, { silent: true })
      } else {
        clearNativeAuthorization()
      }
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
    clearNativeAuthorization()
    setRawSelectedProvider(value)
    const provider = providerByKey.value.get(String(value ?? ''))
    if (!provider) return
    clearProviderForms(provider.key)
    if (authMethods(provider).length > 1) setRawSelectedMethod(provider.key, '')
    else seedDisplayName(provider)
    if (provider.key === 'telegram') void refreshTelegramApplicationStatus()
  }

  async function refreshTelegramApplicationStatus(): Promise<void> {
    const requestId = ++telegramApplicationRequestId
    telegramApplicationBusy.value = true
    telegramApplicationConfigured.value = null
    telegramApplicationError.value = null
    try {
      const status = await callOperation<{ configured: boolean }>('account.application.status', {})
      if (typeof status.configured !== 'boolean') {
        throw new Error('Telegram application status is unavailable.')
      }
      if (requestId === telegramApplicationRequestId) {
        telegramApplicationConfigured.value = status.configured
      }
    } catch (err) {
      if (requestId === telegramApplicationRequestId) {
        telegramApplicationError.value = formatApiError(err, 'Telegram application status is unavailable')
      }
    } finally {
      if (requestId === telegramApplicationRequestId) telegramApplicationBusy.value = false
    }
  }

  function setSelectedMethod(providerKey: string, value: string | number | null): void {
    const provider = providerByKey.value.get(providerKey)
    const methodKey = String(value ?? '')
    if (!provider || !authMethods(provider).some((method) => method.key === methodKey)) return
    clearProviderForms(providerKey)
    clearNativeAuthorization()
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
    markNativeAccountDraftDirty(providerKey, methodKey)
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
    markNativeAccountDraftDirty(providerKey, methodKey)
  }

  function accountDraft(
    provider: SchemaAuthProviderOut,
    method: AuthMethod,
  ): { fields: Record<string, string | number>; displayName: string } | null {
    const fields: Record<string, string | number> = {}
    const errors: Record<string, string> = {}
    if (provider.key === 'telegram' && !editing.value) {
      if (telegramApplicationConfigured.value === null) {
        setProviderMessage(provider.key, 'danger', 'Check Telegram application setup before saving.')
        return null
      }
      if (!telegramApplicationConfigured.value) {
        const apiId = fieldValue(provider.key, method.key, 'api_id').trim()
        const apiHash = fieldValue(provider.key, method.key, 'api_hash').trim()
        if (!/^[1-9]\d*$/.test(apiId) || !Number.isSafeInteger(Number(apiId)))
          errors.api_id = 'Enter a valid application API ID.'
        if (!apiHash) errors.api_hash = 'Application API hash is required.'
        if (apiId && !errors.api_id) fields.api_id = Number(apiId)
        if (apiHash) fields.api_hash = apiHash
      }
    }
    for (const field of method.fields ?? []) {
      if (provider.key === 'telegram' && ['api_id', 'api_hash'].includes(field.key)) continue
      const value = fieldValue(provider.key, method.key, field.key)
      const blank = value.trim() === ''
      const preservedSecret = editing.value && field.secret && editingSecretPresent.value[field.key]
      if (field.required && blank && !preservedSecret)
        errors[field.key] = `${field.label} is required.`
      if (editing.value && field.secret && blank) continue
      if (!editing.value && blank) continue
      fields[field.key] = value
    }
    if (provider.key === 'telegram' && fieldValue(provider.key, method.key, 'proxy_enabled') === 'true') {
      const proxyType = fieldValue(provider.key, method.key, 'proxy_type').trim()
      const proxyHost = fieldValue(provider.key, method.key, 'proxy_host').trim()
      const proxyPort = Number(fieldValue(provider.key, method.key, 'proxy_port'))
      if (!proxyType) errors.proxy_type = 'Choose a proxy type.'
      if (!proxyHost) errors.proxy_host = 'Proxy host is required.'
      if (!Number.isInteger(proxyPort) || proxyPort < 1 || proxyPort > 65535)
        errors.proxy_port = 'Enter a port from 1 to 65535.'
      if (
        proxyType === 'mtproto' &&
        !fieldValue(provider.key, method.key, 'proxy_secret').trim() &&
        !(editing.value && editingSecretPresent.value.proxy_secret)
      ) errors.proxy_secret = 'MTProto proxy secret is required.'
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

  async function saveAccount(
    provider: SchemaAuthProviderOut,
    onNativeSaved?: () => void,
  ): Promise<string | null> {
    const method = selectedMethod(provider)
    if (!method || method.payload_format === 'none') return null
    const draft = accountDraft(provider, method)
    if (!draft) return null
    busyAction.value = providerActionKey(provider.key, 'save')
    try {
      if (editingCredentialRef.value) {
        const credentialRef = editingCredentialRef.value
        const response = await catalogStore.updateCredential(credentialRef, {
          display_name: draft.displayName,
          fields: draft.fields,
        })
        nativeAccountDraftDirty.value = false
        if (isNativeAuthorization(method)) {
          clearProviderMessage(provider.key)
          nativeAuthorizationCredentialRef.value = credentialRef
          nativeAuthorization.value = {
            credential_ref: credentialRef,
            provider_key: provider.key,
            status: response.data.status,
            generation: null,
            challenge: null,
          }
          await refreshNativeAuthorization(credentialRef)
          await refreshNativeSession(credentialRef, { silent: true })
          setAccountMessage(credentialRef, 'success', 'Account updated.')
          onNativeSaved?.()
          return credentialRef
        }
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
      if (provider.key === 'telegram') telegramApplicationConfigured.value = true
      clearForm(provider.key, method.key)
      if (attachProjectId.value) {
        await catalogStore.refreshAuth(attachProjectId.value, { silent: true })
      }
      if (isNativeAuthorization(method)) {
        clearProviderMessage(provider.key)
        nativeAuthorizationCredentialRef.value = credentialRef
        nativeAuthorizationMode.value = 'phone'
        nativeAuthorization.value = {
          credential_ref: credentialRef,
          provider_key: provider.key,
          status: response.data.status,
          generation: null,
          challenge: null,
        }
        if (nativeAccountKind(method) === 'bot' && response.data.setup_required) {
          await refreshNativeAuthorization(credentialRef)
        }
        await refreshNativeSession(credentialRef, { silent: true })
        setAccountMessage(
          credentialRef,
          response.data.setup_required && nativeAccountKind(method) === 'bot' ? 'warning' : 'success',
          nativeAccountKind(method) === 'user'
            ? 'Account saved. Start local authorization when you are ready.'
            : response.data.setup_required
              ? 'Account saved, but bot verification needs attention. Retry in the Account drawer.'
              : 'Telegram bot authorization saved. The session is disconnected.',
        )
        return credentialRef
      }
      try {
        const tested = await catalogStore.testCredential(credentialRef)
        setAccountMessage(
          credentialRef,
          tested.data.ok ? 'success' : 'danger',
          tested.data.ok
            ? credentialTestMessage(provider.key, tested.data.metadata, 'Account verified.')
            : `${credentialVerificationMessage(tested.data)} The Account was saved.`,
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
      if (provider.key === 'telegram' && !editing.value) void refreshTelegramApplicationStatus()
      return null
    } finally {
      busyAction.value = null
    }
  }

  async function saveAndContinueNativeAuthorization(provider: SchemaAuthProviderOut): Promise<string | null> {
    const method = selectedMethod(provider)
    if (provider.key !== 'telegram' || nativeAccountKind(method) !== 'user' || editing.value)
      return null
    const credentialRef = await saveAccount(provider)
    if (!credentialRef) return null
    await startNativeAuthorization(credentialRef, 'phone')
    return credentialRef
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
        authorization_mode: AuthStartRequestAuthorization_mode.phone,
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
          : credentialVerificationMessage(response.data),
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

  async function startNativeAuthorization(
    credentialRef: string,
    mode: NativeAuthorizationMode,
  ): Promise<void> {
    const provider = selectedProvider.value
    const method = provider ? selectedMethod(provider) : null
    if (
      nativeAccountDraftDirty.value ||
      !provider ||
      provider.key !== 'telegram' ||
      !method ||
      !isNativeAuthorization(method)
    )
      return
    if (mode === 'qr' && nativeAccountKind(method) !== 'user') return
    nativeAuthorizationCredentialRef.value = credentialRef
    nativeAuthorizationMode.value = mode
    busyAction.value = connectionActionKey(credentialRef, 'authorize')
    try {
      const response = await catalogStore.startCredential(provider.key, {
        auth_method_key: method.key,
        credential_ref: credentialRef,
        attach_project_id: attachProjectId.value,
        return_surface: attachProjectId.value ? 'project-connections' : 'accounts',
        authorization_mode: requestAuthorizationMode(mode),
      })
      setNativeAuthorizationFromStart(credentialRef, response.data)
      clearProviderMessage(provider.key)
      if (nativeAccountKind(method) === 'bot' && ['connected', 'disconnected'].includes(response.data.status)) {
        setAccountMessage(credentialRef, 'success', 'Telegram bot authorization saved. The session is disconnected.')
      } else {
        clearAccountMessage(credentialRef)
      }
      await refreshNativeSession(credentialRef, { silent: true })
    } catch (err) {
      const message = formatApiError(err, 'failed to start Telegram authorization')
      if (nativeAccountKind(method) === 'bot') await refreshNativeAuthorization(credentialRef)
      if (nativeAuthorization.value?.repair_hint) clearProviderMessage(provider.key)
      else setProviderMessage(provider.key, 'danger', message)
      setAccountMessage(credentialRef, 'danger', message)
    } finally {
      busyAction.value = null
    }
  }

  async function refreshNativeAuthorization(credentialRef?: string): Promise<void> {
    const target = credentialRef ?? nativeAuthorizationCredentialRef.value
    if (!target || nativeAuthorizationCredentialRef.value !== target) return
    busyAction.value = connectionActionKey(target, 'authorize')
    try {
      const state = await catalogStore.getAccountAuthorization(target)
      if (nativeAuthorizationCredentialRef.value === target) nativeAuthorization.value = state
      clearProviderMessage('telegram')
    } catch (err) {
      const message = formatApiError(err, 'failed to refresh Telegram authorization')
      setProviderMessage('telegram', 'danger', message)
      setAccountMessage(target, 'danger', message)
    } finally {
      busyAction.value = null
    }
  }

  async function restartNativeAuthorization(mode: NativeAuthorizationMode): Promise<void> {
    const credentialRef = nativeAuthorizationCredentialRef.value
    if (!credentialRef) return
    await startNativeAuthorization(credentialRef, mode)
  }

  async function submitNativeAuthorization(value: {
    generation: number
    answer: Record<string, string>
  }): Promise<void> {
    const credentialRef = nativeAuthorizationCredentialRef.value
    if (nativeAccountDraftDirty.value || !credentialRef) return
    busyAction.value = connectionActionKey(credentialRef, 'authorize')
    try {
      const response = await catalogStore.submitAccountAuthorization(credentialRef, value)
      if (nativeAuthorizationCredentialRef.value === credentialRef) {
        nativeAuthorization.value = response.data
      }
      clearProviderMessage('telegram')
      clearAccountMessage(credentialRef)
      await refreshNativeSession(credentialRef, { silent: true })
    } catch (err) {
      const message = formatApiError(err, 'Telegram authorization could not continue')
      setProviderMessage('telegram', 'danger', message)
      setAccountMessage(credentialRef, 'danger', message)
    } finally {
      busyAction.value = null
    }
  }

  async function cancelNativeAuthorization(generation: number): Promise<void> {
    const credentialRef = nativeAuthorizationCredentialRef.value
    if (!credentialRef) return
    busyAction.value = connectionActionKey(credentialRef, 'authorize')
    try {
      const response = await catalogStore.cancelAccountAuthorization(credentialRef, generation)
      if (nativeAuthorizationCredentialRef.value === credentialRef) {
        nativeAuthorization.value = response.data
      }
      clearProviderMessage('telegram')
      await refreshNativeSession(credentialRef, { silent: true })
    } catch (err) {
      const message = formatApiError(err, 'Telegram authorization could not be canceled')
      setProviderMessage('telegram', 'danger', message)
      setAccountMessage(credentialRef, 'danger', message)
    } finally {
      busyAction.value = null
    }
  }

  function clearNativeAuthorization(): void {
    nativeAuthorization.value = null
    nativeAuthorizationCredentialRef.value = null
    nativeAuthorizationMode.value = 'phone'
    nativeSession.value = null
  }

  async function refreshNativeSession(
    credentialRef?: string,
    options: { silent?: boolean } = {},
  ): Promise<void> {
    const projectId = attachProjectId.value
    const target = credentialRef ?? nativeAuthorizationCredentialRef.value
    if (!projectId || !target || nativeAuthorizationCredentialRef.value !== target) {
      nativeSession.value = null
      return
    }
    if (!options.silent) busyAction.value = connectionActionKey(target, 'session')
    try {
      const state = await catalogStore.getTelegramAccountSession(projectId, target)
      if (nativeAuthorizationCredentialRef.value === target) nativeSession.value = state
    } catch (err) {
      const message = formatApiError(err, 'failed to load Telegram session status')
      setProviderMessage('telegram', 'danger', message)
      setAccountMessage(target, 'danger', message)
    } finally {
      if (!options.silent) busyAction.value = null
    }
  }

  async function connectNativeSession(): Promise<void> {
    const projectId = attachProjectId.value
    const credentialRef = nativeAuthorizationCredentialRef.value
    if (nativeAccountDraftDirty.value || !projectId || !credentialRef) return
    busyAction.value = connectionActionKey(credentialRef, 'session')
    try {
      nativeSession.value = await catalogStore.connectTelegramAccountSession(projectId, credentialRef)
      setAccountMessage(credentialRef, 'success', 'Telegram session connect requested.')
    } catch (err) {
      const message = formatApiError(err, 'failed to connect the Telegram session')
      setProviderMessage('telegram', 'danger', message)
      setAccountMessage(credentialRef, 'danger', message)
    } finally {
      busyAction.value = null
    }
  }

  async function disconnectNativeSession(): Promise<void> {
    const projectId = attachProjectId.value
    const credentialRef = nativeAuthorizationCredentialRef.value
    if (!projectId || !credentialRef) return
    busyAction.value = connectionActionKey(credentialRef, 'session')
    try {
      nativeSession.value = await catalogStore.disconnectTelegramAccountSession(projectId, credentialRef)
      setAccountMessage(credentialRef, 'success', 'Telegram session disconnected.')
    } catch (err) {
      const message = formatApiError(err, 'failed to disconnect the Telegram session')
      setProviderMessage('telegram', 'danger', message)
      setAccountMessage(credentialRef, 'danger', message)
    } finally {
      busyAction.value = null
    }
  }

  function setNativeAuthorizationMode(mode: NativeAuthorizationMode): void {
    if (mode === 'qr' && !nativeAuthorizationAllowsQr.value) return
    nativeAuthorizationMode.value = mode
  }

  function markNativeAccountDraftDirty(providerKey: string, methodKey: string): void {
    const provider = selectedProvider.value
    const method = provider ? selectedMethod(provider) : null
    if (
      editing.value &&
      provider?.key === providerKey &&
      method?.key === methodKey &&
      isNativeAuthorization(method)
    ) {
      nativeAccountDraftDirty.value = true
    }
  }

  function resetNativeAccountDraft(): void {
    nativeAccountDraftDirty.value = false
  }

  function setNativeAuthorizationFromStart(
    credentialRef: string,
    result: SchemaAuthStartOut,
  ): void {
    if (
      result.credential_ref !== credentialRef ||
      nativeAuthorizationCredentialRef.value !== credentialRef
    ) {
      return
    }
    nativeAuthorization.value = {
      credential_ref: credentialRef,
      provider_key: result.provider_key,
      status: result.status,
      generation: result.challenge?.generation ?? null,
      challenge: result.challenge ?? null,
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

  function clearProviderMessage(providerKey: string): void {
    if (!providerMessages.value[providerKey]) return
    const next = { ...providerMessages.value }
    delete next[providerKey]
    providerMessages.value = next
  }

  function setAccountMessage(credentialRef: string, tone: MessageTone, text: string): void {
    accountMessages.value = { ...accountMessages.value, [credentialRef]: { tone, text } }
  }

  function clearAccountMessage(credentialRef: string): void {
    if (!accountMessages.value[credentialRef]) return
    const next = { ...accountMessages.value }
    delete next[credentialRef]
    accountMessages.value = next
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
    nativeAuthorization,
    nativeAuthorizationActive,
    nativeAuthorizationMode,
    nativeAuthorizationAllowsQr,
    nativeAuthorizationBusy,
    nativeSession,
    nativeSessionActive,
    nativeSessionBusy,
    nativeAccountDraftDirty,
    telegramApplicationConfigured,
    telegramApplicationBusy,
    telegramApplicationError,
    refreshTelegramApplicationStatus,
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
    resetNativeAccountDraft,
    saveAccount,
    saveAndContinueNativeAuthorization,
    startProvider,
    startNativeAuthorization,
    restartNativeAuthorization,
    setNativeAuthorizationMode,
    refreshNativeAuthorization,
    refreshNativeSession,
    connectNativeSession,
    disconnectNativeSession,
    submitNativeAuthorization,
    cancelNativeAuthorization,
    testAccount,
    requestRevoke,
    confirmRevoke,
    applyOAuthReturn,
    connectionActionKey,
  }
}

function isNativeAuthorization(method: AuthMethod | null): boolean {
  return method?.config?.native_authorization === true
}

function nativeAccountKind(method: AuthMethod | null): 'bot' | 'user' | null {
  const kind = method?.config?.account_kind
  return kind === 'bot' || kind === 'user' ? kind : null
}

function requestAuthorizationMode(
  mode: NativeAuthorizationMode,
): SchemaAuthStartRequest['authorization_mode'] {
  return mode === 'qr'
    ? AuthStartRequestAuthorization_mode.qr
    : AuthStartRequestAuthorization_mode.phone
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
