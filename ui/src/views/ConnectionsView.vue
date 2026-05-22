<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiFormField,
  UiInput,
  UiJsonBlock,
  UiPageShell,
  UiPanel,
  UiSecretInput,
  UiSectionHeader,
  UiSelect,
} from '@/components/ui'
import type { SchemaAuthProviderOut, SchemaCredentialConnectionOut } from '@/api'
import type { DataTableColumn } from '@/components/types'
import { formatApiError } from '@/lib/client'
import { sanitizeForDisplay } from '@/lib/stackos/json'
import { useStackOsCatalogStore } from '@/stores/plugins'

type ConnectionRow = SchemaCredentialConnectionOut & { id: string }
type AuthMethod = NonNullable<SchemaAuthProviderOut['auth_methods']>[number]
type AuthField = NonNullable<AuthMethod['fields']>[number]

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { authProviders, authStatus, loading, error } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const selectedMethodByProvider = ref<Record<string, string>>({})
const selectedConnectionByProvider = ref<Record<string, string>>({})
const labelByForm = ref<Record<string, string>>({})
const profileByForm = ref<Record<string, string>>({})
const fieldsByForm = ref<Record<string, Record<string, string>>>({})
const busyAction = ref<string | null>(null)
const providerMessages = ref<Record<string, { tone: 'success' | 'danger' | 'info'; text: string }>>(
  {},
)
const connections = computed<ConnectionRow[]>(() =>
  (authStatus.value?.connections ?? []).map((connection) => ({
    ...connection,
    id: connection.credential_ref,
  })),
)

const columns: DataTableColumn<ConnectionRow>[] = [
  { key: 'provider_key', label: 'Provider' },
  { key: 'profile_key', label: 'Profile', widthClass: 'w-32' },
  { key: 'status', label: 'Status', widthClass: 'w-32' },
  { key: 'auth_method_key', label: 'Method', widthClass: 'w-36' },
  { key: 'label', label: 'Label', format: (value) => String(value ?? '-') },
  { key: 'credential_ref', label: 'Credential ref' },
  { key: 'expires_at', label: 'Expires', format: (value) => String(value ?? '-') },
]

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await catalogStore.refreshAuth(projectId.value)
}

function authMethods(provider: SchemaAuthProviderOut): AuthMethod[] {
  return provider.auth_methods ?? []
}

function selectedMethodKey(provider: SchemaAuthProviderOut): string {
  return selectedMethodByProvider.value[provider.key] ?? authMethods(provider)[0]?.key ?? ''
}

function selectedMethod(provider: SchemaAuthProviderOut): AuthMethod | null {
  const key = selectedMethodKey(provider)
  return authMethods(provider).find((method) => method.key === key) ?? authMethods(provider)[0] ?? null
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

function inputType(field: AuthField): 'text' | 'url' | 'number' {
  if (field.type === 'url') return 'url'
  if (field.type === 'number') return 'number'
  return 'text'
}

function isSecretField(field: AuthField): boolean {
  return field.secret || ['secret', 'password'].includes(field.type)
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

function activeConnectionsFor(providerKey: string): SchemaCredentialConnectionOut[] {
  return (authStatus.value?.connections ?? []).filter(
    (connection) => connection.provider_key === providerKey && connection.revoked_at === null,
  )
}

function selectedConnectionFor(providerKey: string): SchemaCredentialConnectionOut | null {
  const active = activeConnectionsFor(providerKey)
  const selectedRef = selectedConnectionByProvider.value[providerKey]
  return (
    active.find((connection) => connection.credential_ref === selectedRef) ?? active[0] ?? null
  )
}

function setSelectedConnection(providerKey: string, value: string | number | null): void {
  selectedConnectionByProvider.value = {
    ...selectedConnectionByProvider.value,
    [providerKey]: String(value ?? ''),
  }
}

function connectionLabel(connection: SchemaCredentialConnectionOut): string {
  const parts = [connection.profile_key]
  if (connection.label) parts.push(connection.label)
  parts.push(connection.credential_ref)
  return parts.join(' · ')
}

function actionKey(providerKey: string, action: string): string {
  return `${providerKey}:${action}`
}

function isBusy(providerKey: string, action: string): boolean {
  return busyAction.value === actionKey(providerKey, action)
}

function setProviderMessage(
  providerKey: string,
  tone: 'success' | 'danger' | 'info',
  text: string,
): void {
  providerMessages.value = {
    ...providerMessages.value,
    [providerKey]: { tone, text },
  }
}

function credentialFields(provider: SchemaAuthProviderOut, method: AuthMethod): Record<string, string> | null {
  const fields: Record<string, string> = {}
  for (const field of method.fields ?? []) {
    const value = fieldValue(provider.key, method.key, field.key).trim()
    if (field.required && !value) {
      setProviderMessage(provider.key, 'danger', `${field.label} is required.`)
      return null
    }
    if (value) fields[field.key] = value
  }
  return fields
}

async function saveCredential(provider: SchemaAuthProviderOut): Promise<void> {
  const method = selectedMethod(provider)
  if (!method || method.payload_format === 'none') return
  const fields = credentialFields(provider, method)
  if (fields === null) return
  const key = formKey(provider.key, method.key)
  const profileKey = (profileByForm.value[key] ?? 'default').trim() || 'default'
  const label = (labelByForm.value[key] ?? '').trim()
  if (Object.keys(fields).length === 0 && (method.fields ?? []).some((field) => field.secret)) {
    setProviderMessage(provider.key, 'danger', 'Credential fields are required.')
    return
  }
  busyAction.value = actionKey(provider.key, 'save')
  try {
    const response = await catalogStore.storeCredential(projectId.value, provider.key, {
      auth_method_key: method.key,
      profile_key: profileKey,
      label: label || null,
      fields,
    })
    fieldsByForm.value = { ...fieldsByForm.value, [key]: {} }
    setProviderMessage(provider.key, 'success', `Stored ${response.data.credential_ref}.`)
  } catch (err) {
    setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to store credential'))
  } finally {
    busyAction.value = null
  }
}

async function startProvider(provider: SchemaAuthProviderOut): Promise<void> {
  const method = selectedMethod(provider)
  if (!method) return
  busyAction.value = actionKey(provider.key, 'start')
  try {
    const response = await catalogStore.startCredential(projectId.value, provider.key, {
      auth_method_key: method.key,
      redirect_uri: null,
    })
    const url = response.data.authorization_url ?? response.data.setup_url
    setProviderMessage(
      provider.key,
      'info',
      url ? `Setup URL ready: ${url}` : `Started ${response.data.status}.`,
    )
  } catch (err) {
    setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to start auth flow'))
  } finally {
    busyAction.value = null
  }
}

async function testProvider(provider: SchemaAuthProviderOut): Promise<void> {
  const connection = selectedConnectionFor(provider.key)
  if (!connection) return
  busyAction.value = actionKey(provider.key, 'test')
  try {
    const response = await catalogStore.testCredential(projectId.value, {
      credential_ref: connection.credential_ref,
    })
    setProviderMessage(
      provider.key,
      response.data.ok ? 'success' : 'danger',
      response.data.summary,
    )
  } catch (err) {
    setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to test credential'))
  } finally {
    busyAction.value = null
  }
}

async function revokeProvider(provider: SchemaAuthProviderOut): Promise<void> {
  const connection = selectedConnectionFor(provider.key)
  if (!connection) return
  busyAction.value = actionKey(provider.key, 'revoke')
  try {
    await catalogStore.revokeCredential(projectId.value, {
      credential_ref: connection.credential_ref,
    })
    setProviderMessage(provider.key, 'info', `Revoked ${connection.credential_ref}.`)
  } catch (err) {
    setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to revoke credential'))
  } finally {
    busyAction.value = null
  }
}

onMounted(load)
watch(projectId, load)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Connections"
      description="Sanitized provider auth state and opaque credential references."
      :breadcrumbs="[{ label: 'Connections' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Credential Refs"
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ connections.length }}</UiBadge>
        </template>
      </UiSectionHeader>
      <DataTable
        :items="connections"
        :columns="columns"
        :loading="loading"
        aria-label="Connections"
        empty-message="No credentials connected."
      >
        <template #cell:provider_key="{ value }">
          <UiBadge tone="accent">{{ value }}</UiBadge>
        </template>
        <template #cell:status="{ value, row }">
          <UiBadge :tone="(row as ConnectionRow).setup_required ? 'warning' : 'success'">
            {{ value }}
          </UiBadge>
        </template>
      </DataTable>
    </UiPanel>

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Providers"
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ authProviders.length }}</UiBadge>
        </template>
      </UiSectionHeader>
      <ul class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        <li
          v-for="provider in authProviders"
          :key="provider.key"
          class="rounded-md border border-subtle bg-bg-surface p-3"
        >
          <div class="mb-1 flex items-center justify-between gap-2">
            <span class="font-medium">{{ provider.name }}</span>
            <UiBadge>{{ provider.auth_type }}</UiBadge>
          </div>
          <div class="flex flex-wrap items-center gap-1.5 text-sm text-fg-muted">
            <span>{{ provider.key }}</span>
            <UiBadge
              v-if="activeConnectionsFor(provider.key).length > 0"
              tone="success"
            >
              {{ activeConnectionsFor(provider.key).length }} connected
            </UiBadge>
          </div>

          <div
            v-if="supportsCredential(provider) && selectedMethod(provider)"
            class="mt-3 grid gap-2"
          >
            <UiFormField
              v-if="authMethods(provider).length > 1"
              label="Auth Method"
            >
              <template #default="{ id, describedBy, invalid }">
                <UiSelect
                  :id="id"
                  :model-value="selectedMethodKey(provider)"
                  :options="
                    authMethods(provider).map((method) => ({
                      value: method.key,
                      label: method.label,
                    }))
                  "
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  size="sm"
                  @update:model-value="setSelectedMethod(provider.key, $event)"
                />
              </template>
            </UiFormField>
            <UiFormField label="Profile">
              <template #default="{ id, describedBy, invalid }">
                <UiInput
                  :id="id"
                  v-model="profileByForm[formKey(provider.key, selectedMethod(provider)?.key ?? '')]"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  placeholder="default"
                  size="sm"
                />
              </template>
            </UiFormField>
            <UiFormField label="Label">
              <template #default="{ id, describedBy, invalid }">
                <UiInput
                  :id="id"
                  v-model="labelByForm[formKey(provider.key, selectedMethod(provider)?.key ?? '')]"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  placeholder="Primary"
                  size="sm"
                />
              </template>
            </UiFormField>
            <UiFormField
              v-for="field in selectedMethod(provider)?.fields ?? []"
              :key="field.key"
              :label="field.label"
              :help="field.description ?? undefined"
              :required="field.required"
            >
              <template #default="{ id, describedBy, invalid }">
                <UiSecretInput
                  v-if="isSecretField(field)"
                  :id="id"
                  :model-value="
                    fieldValue(provider.key, selectedMethod(provider)?.key ?? '', field.key)
                  "
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  no-copy
                  no-reveal
                  :placeholder="field.placeholder ?? ''"
                  size="sm"
                  @update:model-value="
                    setFieldValue(provider.key, selectedMethod(provider)?.key ?? '', field.key, $event)
                  "
                />
                <UiInput
                  v-else
                  :id="id"
                  :model-value="
                    fieldValue(provider.key, selectedMethod(provider)?.key ?? '', field.key)
                  "
                  :type="inputType(field)"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  :placeholder="field.placeholder ?? undefined"
                  size="sm"
                  @update:model-value="
                    setFieldValue(provider.key, selectedMethod(provider)?.key ?? '', field.key, $event)
                  "
                />
              </template>
            </UiFormField>
            <div class="flex flex-wrap gap-2">
              <UiFormField
                v-if="activeConnectionsFor(provider.key).length > 0"
                label="Credential Profile"
                class="basis-full"
              >
                <template #default="{ id, describedBy, invalid }">
                  <UiSelect
                    :id="id"
                    :model-value="selectedConnectionFor(provider.key)?.credential_ref ?? null"
                    :options="
                      activeConnectionsFor(provider.key).map((connection) => ({
                        value: connection.credential_ref,
                        label: connectionLabel(connection),
                      }))
                    "
                    :aria-describedby="describedBy"
                    :invalid="invalid"
                    size="sm"
                    @update:model-value="setSelectedConnection(provider.key, $event)"
                  />
                </template>
              </UiFormField>
              <UiButton
                v-if="selectedMethod(provider)?.interactive"
                size="sm"
                variant="secondary"
                icon-left="external-link"
                :loading="isBusy(provider.key, 'start')"
                @click="startProvider(provider)"
              >
                Start
              </UiButton>
              <UiButton
                size="sm"
                variant="primary"
                icon-left="save"
                :loading="isBusy(provider.key, 'save')"
                :disabled="selectedMethod(provider)?.payload_format === 'none'"
                @click="saveCredential(provider)"
              >
                Save
              </UiButton>
              <UiButton
                size="sm"
                icon-left="plug-zap"
                :loading="isBusy(provider.key, 'test')"
                :disabled="!selectedConnectionFor(provider.key)"
                @click="testProvider(provider)"
              >
                Test
              </UiButton>
              <UiButton
                size="sm"
                variant="danger"
                icon-left="ban"
                :loading="isBusy(provider.key, 'revoke')"
                :disabled="!selectedConnectionFor(provider.key)"
                @click="revokeProvider(provider)"
              >
                Revoke
              </UiButton>
            </div>
          </div>
          <UiCallout
            v-else
            tone="info"
            class="mt-3"
          >
            No credential required.
          </UiCallout>
          <UiCallout
            v-if="providerMessages[provider.key]"
            :tone="providerMessages[provider.key].tone"
            class="mt-3"
          >
            {{ providerMessages[provider.key].text }}
          </UiCallout>
        </li>
      </ul>
    </UiPanel>

    <details
      v-if="authStatus"
      class="rounded-md border border-default bg-bg-surface shadow-xs"
    >
      <summary class="cursor-pointer px-4 py-3 text-sm font-semibold text-fg-strong focus-ring">
        Diagnostics
      </summary>
      <div class="border-t border-subtle p-3">
        <UiJsonBlock
          :data="sanitizeForDisplay(authStatus)"
          density="compact"
          max-height="18rem"
          wrap
        />
      </div>
    </details>
  </UiPageShell>
</template>
