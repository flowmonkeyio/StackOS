import { computed, nextTick, ref, type ComputedRef } from 'vue'
import { storeToRefs } from 'pinia'

import type { SchemaAuthProviderOut } from '@/api'
import { useConnectionForm } from '@/composables/useConnectionForm'
import { formatApiError } from '@/lib/client'
import { useStackOsCatalogStore } from '@/stores/plugins'

import {
  compareConnections,
  connectionActionKey,
  connectionNeedsAttention,
  connectionStatusKey,
  credentialTestMessage,
  providerActionKey,
  providerGroupLabel,
  serviceName,
} from './formatters'
import { connectionFieldInputId } from './fieldIds'
import type {
  AuthMethod,
  ConnectionRow,
  MessageMap,
  MessageTone,
  ServiceGroup,
} from './types'

export function useConnectionCredentials(projectId: ComputedRef<number>) {
  const catalogStore = useStackOsCatalogStore()
  const { authProviders, authStatus, enabledPlugins, loading, error } = storeToRefs(catalogStore)
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
    profileValue,
    setProfileValue,
    labelValue,
    setLabelValue,
    setSelectedProvider: setRawSelectedProvider,
    clearForm,
    populateForm,
  } = useConnectionForm()

  const addPanelOpen = ref(false)
  const busyAction = ref<string | null>(null)
  const providerMessages = ref<MessageMap>({})
  const providerSetupUrls = ref<Record<string, string>>({})
  const connectionMessages = ref<MessageMap>({})
  const fieldErrors = ref<Record<string, string>>({})
  const pendingRevoke = ref<ConnectionRow | null>(null)
  const editingCredentialRef = ref<string | null>(null)
  const editingSecretPresent = ref<Record<string, boolean>>({})
  const editing = computed(() => editingCredentialRef.value !== null)

  const connections = computed<ConnectionRow[]>(() =>
    (authStatus.value?.connections ?? []).map((connection) => ({
      ...connection,
      id: connection.credential_ref,
    })),
  )
  const providerByKey = computed(() => {
    const rows = new Map<string, SchemaAuthProviderOut>()
    for (const provider of authProviders.value) rows.set(provider.key, provider)
    return rows
  })
  const visibleAuthProviders = computed(() => {
    const enabledPluginSlugs = new Set(enabledPlugins.value.map((plugin) => plugin.slug))
    if (enabledPluginSlugs.size === 0) return []
    return authProviders.value.filter(
      (provider) =>
        canAddProvider(provider) &&
        (!provider.plugin_slug || enabledPluginSlugs.has(provider.plugin_slug)),
    )
  })
  const visibleProviderByKey = computed(() => {
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
      const provider = visibleProviderByKey.value.get(selectedProviderKey.value)
      if (provider) return provider
    }
    return visibleAuthProviders.value[0] ?? null
  })
  const activeConnections = computed(() =>
    connections.value.filter((connection) => connection.revoked_at === null),
  )
  const connectedConnections = computed(() =>
    activeConnections.value.filter((connection) => connectionStatusKey(connection) === 'connected'),
  )
  const attentionConnections = computed(() =>
    activeConnections.value.filter(connectionNeedsAttention),
  )
  const serviceGroups = computed<ServiceGroup[]>(() => {
    const grouped = new Map<string, ConnectionRow[]>()
    for (const connection of activeConnections.value) {
      const rows = grouped.get(connection.provider_key) ?? []
      rows.push(connection)
      grouped.set(connection.provider_key, rows)
    }
    return Array.from(grouped.entries())
      .map(([providerKey, rows]) => ({
        providerKey,
        provider: providerByKey.value.get(providerKey) ?? null,
        connections: [...rows].sort(compareConnections),
      }))
      .sort((left, right) => {
        const leftNeedsAttention = left.connections.some(connectionNeedsAttention)
        const rightNeedsAttention = right.connections.some(connectionNeedsAttention)
        if (leftNeedsAttention !== rightNeedsAttention) return leftNeedsAttention ? -1 : 1
        return serviceName(left).localeCompare(serviceName(right))
      })
  })
  const connectedServiceCount = computed(
    () => new Set(connectedConnections.value.map((connection) => connection.provider_key)).size,
  )

  async function load(): Promise<void> {
    await catalogStore.refreshPlugins(projectId.value)
    const pluginError = error.value
    await catalogStore.refreshAuth(projectId.value)
    const authError = error.value
    error.value = [pluginError, authError].filter(Boolean).join(' ') || null
  }

  function ensureSelectableProvider(): void {
    const providers = visibleAuthProviders.value
    if (!providers.length) {
      selectedProviderKey.value = ''
      return
    }
    if (!selectedProviderKey.value && providers[0]) {
      selectedProviderKey.value = providers[0].key
      return
    }
    if (
      selectedProviderKey.value &&
      !providers.some((provider) => provider.key === selectedProviderKey.value)
    ) {
      selectedProviderKey.value = providers[0].key
    }
  }

  function applyProviderSelection(value: unknown): void {
    ensureSelectableProvider()
    const providerKey = typeof value === 'string' ? value : ''
    if (!providerKey || !visibleProviderByKey.value.has(providerKey)) return
    openAddConnection(providerKey)
  }

  function openAddConnection(providerKey?: string): void {
    editingCredentialRef.value = null
    editingSecretPresent.value = {}
    if (providerKey && visibleProviderByKey.value.has(providerKey)) {
      selectProvider(providerKey)
    }
    ensureSelectableProvider()
    const provider = selectedProvider.value
    const method = provider ? selectedMethod(provider) : null
    if (provider && method) clearForm(provider.key, method.key)
    addPanelOpen.value = true
  }

  async function openEditConnection(connection: ConnectionRow): Promise<void> {
    busyAction.value = connectionActionKey(connection.credential_ref, 'edit')
    try {
      const state = await catalogStore.getCredential(projectId.value, connection.credential_ref)
      const provider = visibleProviderByKey.value.get(state.connection.provider_key)
      if (!provider) {
        throw new Error('The provider plugin must be enabled before this connection can be edited.')
      }
      const method = authMethods(provider).find(
        (candidate) => candidate.key === state.connection.auth_method_key,
      )
      if (!method) {
        throw new Error('The saved authentication method is no longer available.')
      }
      setRawSelectedProvider(provider.key)
      setRawSelectedMethod(provider.key, method.key)
      const values: Record<string, string> = {}
      for (const [key, value] of Object.entries(state.values)) {
        if (value !== null && ['string', 'number', 'boolean'].includes(typeof value)) {
          values[key] = String(value)
        }
      }
      populateForm(
        provider.key,
        method.key,
        values,
        state.connection.profile_key,
        state.connection.label ?? '',
      )
      editingCredentialRef.value = connection.credential_ref
      editingSecretPresent.value = state.secret_present
      fieldErrors.value = {}
      addPanelOpen.value = true
    } catch (err) {
      setConnectionMessage(
        connection.credential_ref,
        'danger',
        formatApiError(err, 'failed to load credential settings'),
      )
    } finally {
      busyAction.value = null
    }
  }

  function selectProvider(value: string | number | null): void {
    fieldErrors.value = {}
    setRawSelectedProvider(value)
  }

  function setSelectedMethod(providerKey: string, value: string | number | null): void {
    fieldErrors.value = {}
    setRawSelectedMethod(providerKey, value)
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

  function credentialFields(
    provider: SchemaAuthProviderOut,
    method: AuthMethod,
  ): Record<string, string> | null {
    const fields: Record<string, string> = {}
    const errors: Record<string, string> = {}
    for (const field of method.fields ?? []) {
      const value = fieldValue(provider.key, method.key, field.key)
      const blank = value.trim() === ''
      const preservedSecret = editing.value && field.secret && editingSecretPresent.value[field.key]
      if (field.required && blank && !preservedSecret) {
        errors[field.key] = `${field.label} is required.`
      }
      if (editing.value && field.secret && blank) continue
      if (!editing.value && blank) continue
      fields[field.key] = value
    }
    fieldErrors.value = errors
    const firstInvalidKey = Object.keys(errors)[0]
    if (firstInvalidKey) {
      setProviderMessage(provider.key, 'danger', 'Complete the required fields to continue.')
      void nextTick(() => document.getElementById(connectionFieldInputId(firstInvalidKey))?.focus())
      return null
    }
    return fields
  }

  async function saveCredential(provider: SchemaAuthProviderOut): Promise<void> {
    const method = selectedMethod(provider)
    if (!method || method.payload_format === 'none') return
    const fields = credentialFields(provider, method)
    if (fields === null) return
    const profileKey = profileValue(provider.key, method.key).trim() || 'default'
    const label = labelValue(provider.key, method.key).trim()
    if (
      !editing.value &&
      Object.keys(fields).length === 0 &&
      (method.fields ?? []).some((field) => field.secret)
    ) {
      setProviderMessage(provider.key, 'danger', 'Credential fields are required.')
      return
    }
    busyAction.value = providerActionKey(provider.key, 'save')
    try {
      if (editingCredentialRef.value) {
        const credentialRef = editingCredentialRef.value
        await catalogStore.updateCredential(projectId.value, credentialRef, {
          label: label || null,
          fields,
        })
        clearForm(provider.key, method.key)
        editingCredentialRef.value = null
        editingSecretPresent.value = {}
        fieldErrors.value = {}
        setConnectionMessage(credentialRef, 'success', 'Connection settings updated.')
        addPanelOpen.value = false
        return
      }
      const response = await catalogStore.storeCredential(projectId.value, provider.key, {
        auth_method_key: method.key,
        profile_key: profileKey,
        label: label || null,
        fields,
      })
      clearForm(provider.key, method.key)
      fieldErrors.value = {}
      try {
        const tested = await catalogStore.testCredential(projectId.value, {
          credential_ref: response.data.credential_ref,
        })
        if (tested.data.ok) {
          setProviderMessage(
            provider.key,
            'success',
            credentialTestMessage(
              provider.key,
              tested.data.metadata,
              `Connected ${response.data.credential_ref}.`,
            ),
          )
          addPanelOpen.value = false
          return
        }
        setConnectionMessage(
          response.data.credential_ref,
          'danger',
          `${tested.data.summary} The credential was saved; retry verification from Services.`,
        )
        addPanelOpen.value = false
      } catch (testError) {
        setConnectionMessage(
          response.data.credential_ref,
          'danger',
          `Stored, but verification did not complete. ${formatApiError(testError, 'Retry from Services.')}`,
        )
        addPanelOpen.value = false
      }
    } catch (err) {
      setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to store credential'))
    } finally {
      busyAction.value = null
    }
  }

  async function startProvider(provider: SchemaAuthProviderOut): Promise<void> {
    const method = selectedMethod(provider)
    if (!method) return
    busyAction.value = providerActionKey(provider.key, 'start')
    try {
      const response = await catalogStore.startCredential(projectId.value, provider.key, {
        auth_method_key: method.key,
        redirect_uri: null,
      })
      const url = response.data.authorization_url ?? response.data.setup_url
      const safeUrl = safeSetupUrl(url)
      if (url && !safeUrl) {
        setProviderMessage(provider.key, 'danger', 'The provider returned an invalid setup URL.')
        return
      }
      if (safeUrl) providerSetupUrls.value[provider.key] = safeUrl
      setProviderMessage(
        provider.key,
        'info',
        safeUrl
          ? 'Setup is ready. Continue with the provider.'
          : `Started ${response.data.status}.`,
      )
    } catch (err) {
      setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to start auth flow'))
    } finally {
      busyAction.value = null
    }
  }

  async function testConnection(connection: ConnectionRow): Promise<void> {
    busyAction.value = connectionActionKey(connection.credential_ref, 'test')
    try {
      const response = await catalogStore.testCredential(projectId.value, {
        credential_ref: connection.credential_ref,
      })
      setConnectionMessage(
        connection.credential_ref,
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
      setConnectionMessage(
        connection.credential_ref,
        'danger',
        formatApiError(err, 'failed to test credential'),
      )
    } finally {
      busyAction.value = null
    }
  }

  function requestRevoke(connection: ConnectionRow): void {
    pendingRevoke.value = connection
  }

  async function confirmRevoke(): Promise<void> {
    const connection = pendingRevoke.value
    if (!connection) return
    busyAction.value = connectionActionKey(connection.credential_ref, 'revoke')
    try {
      await catalogStore.revokeCredential(projectId.value, {
        credential_ref: connection.credential_ref,
      })
    } catch (err) {
      setConnectionMessage(
        connection.credential_ref,
        'danger',
        formatApiError(err, 'failed to revoke credential'),
      )
    } finally {
      busyAction.value = null
      pendingRevoke.value = null
    }
  }

  function setProviderMessage(providerKey: string, tone: MessageTone, text: string): void {
    providerMessages.value = { ...providerMessages.value, [providerKey]: { tone, text } }
  }

  function setConnectionMessage(credentialRef: string, tone: MessageTone, text: string): void {
    connectionMessages.value = {
      ...connectionMessages.value,
      [credentialRef]: { tone, text },
    }
  }

  return {
    authStatus,
    loading,
    error,
    addPanelOpen,
    busyAction,
    providerMessages,
    providerSetupUrls,
    connectionMessages,
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
    profileValue,
    setProfileValue,
    labelValue,
    setLabelValue,
    setSelectedProvider: selectProvider,
    connections,
    visibleAuthProviders,
    providerOptions,
    selectedProvider,
    activeConnections,
    connectedConnections,
    attentionConnections,
    serviceGroups,
    connectedServiceCount,
    load,
    applyProviderSelection,
    openAddConnection,
    openEditConnection,
    saveCredential,
    startProvider,
    testConnection,
    requestRevoke,
    confirmRevoke,
    connectionActionKey,
  }
}

function safeSetupUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const url = new URL(value, window.location.origin)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}
