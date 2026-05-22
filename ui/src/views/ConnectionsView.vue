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
} from '@/components/ui'
import type { SchemaAuthProviderOut, SchemaCredentialConnectionOut } from '@/api'
import type { DataTableColumn } from '@/components/types'
import { formatApiError } from '@/lib/client'
import { sanitizeForDisplay } from '@/lib/stackos/json'
import { useStackOsCatalogStore } from '@/stores/plugins'

type ConnectionRow = SchemaCredentialConnectionOut & { id: string }
type SetupField = {
  key: string
  label: string
  type?: 'text' | 'url'
  required?: boolean
  placeholder?: string
  description?: string
}

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { authProviders, authStatus, loading, error } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const secretByProvider = ref<Record<string, string>>({})
const labelByProvider = ref<Record<string, string>>({})
const configByProvider = ref<Record<string, Record<string, string>>>({})
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
  { key: 'status', label: 'Status', widthClass: 'w-32' },
  { key: 'auth_type', label: 'Auth', widthClass: 'w-28' },
  { key: 'credential_ref', label: 'Credential ref' },
  { key: 'expires_at', label: 'Expires', format: (value) => String(value ?? '-') },
]

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await catalogStore.refreshAuth(projectId.value)
}

function supportsSecret(provider: SchemaAuthProviderOut): boolean {
  return !['none', 'local'].includes(provider.auth_type)
}

function setupFields(provider: SchemaAuthProviderOut): SetupField[] {
  const fields = provider.config_json?.setup_fields
  if (!Array.isArray(fields)) return []
  return fields.filter(isSetupField)
}

function isSetupField(value: unknown): value is SetupField {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const field = value as Record<string, unknown>
  return typeof field.key === 'string' && typeof field.label === 'string'
}

function inputType(field: SetupField): 'text' | 'url' {
  return field.type === 'url' ? 'url' : 'text'
}

function configFieldValue(providerKey: string, fieldKey: string): string {
  return configByProvider.value[providerKey]?.[fieldKey] ?? ''
}

function setConfigField(
  providerKey: string,
  fieldKey: string,
  value: string | number | null,
): void {
  configByProvider.value = {
    ...configByProvider.value,
    [providerKey]: {
      ...(configByProvider.value[providerKey] ?? {}),
      [fieldKey]: value === null ? '' : String(value),
    },
  }
}

function connectionFor(providerKey: string): SchemaCredentialConnectionOut | null {
  return (
    (authStatus.value?.connections ?? []).find(
      (connection) => connection.provider_key === providerKey && connection.revoked_at === null,
    ) ?? null
  )
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

function credentialConfig(provider: SchemaAuthProviderOut): Record<string, string> | null {
  const config: Record<string, string> = {}
  const label = (labelByProvider.value[provider.key] ?? '').trim()
  if (label) config.label = label
  for (const field of setupFields(provider)) {
    const value = configFieldValue(provider.key, field.key).trim()
    if (field.required && !value) {
      setProviderMessage(provider.key, 'danger', `${field.label} is required.`)
      return null
    }
    if (value) config[field.key] = value
  }
  return config
}

async function saveCredential(provider: SchemaAuthProviderOut): Promise<void> {
  if (!supportsSecret(provider)) return
  const secret = (secretByProvider.value[provider.key] ?? '').trim()
  if (!secret) {
    setProviderMessage(provider.key, 'danger', 'Secret is required.')
    return
  }
  const config = credentialConfig(provider)
  if (config === null) return
  busyAction.value = actionKey(provider.key, 'save')
  try {
    const response = await catalogStore.storeCredential(projectId.value, provider.key, {
      plaintext_payload: secret,
      payload_encoding: 'plain',
      config_json: Object.keys(config).length > 0 ? config : null,
    })
    secretByProvider.value = { ...secretByProvider.value, [provider.key]: '' }
    setProviderMessage(provider.key, 'success', `Stored ${response.data.credential_ref}.`)
  } catch (err) {
    setProviderMessage(provider.key, 'danger', formatApiError(err, 'failed to store credential'))
  } finally {
    busyAction.value = null
  }
}

async function testProvider(provider: SchemaAuthProviderOut): Promise<void> {
  const connection = connectionFor(provider.key)
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
  const connection = connectionFor(provider.key)
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
              v-if="connectionFor(provider.key)"
              tone="success"
            >
              {{ connectionFor(provider.key)?.status }}
            </UiBadge>
          </div>

          <div
            v-if="supportsSecret(provider)"
            class="mt-3 grid gap-2"
          >
            <UiFormField label="Secret">
              <template #default="{ id, describedBy, invalid }">
                <UiSecretInput
                  :id="id"
                  v-model="secretByProvider[provider.key]"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  no-copy
                  no-reveal
                  placeholder="Paste credential"
                  size="sm"
                />
              </template>
            </UiFormField>
            <UiFormField label="Label">
              <template #default="{ id, describedBy, invalid }">
                <UiInput
                  :id="id"
                  v-model="labelByProvider[provider.key]"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  placeholder="Primary"
                  size="sm"
                />
              </template>
            </UiFormField>
            <UiFormField
              v-for="field in setupFields(provider)"
              :key="field.key"
              :label="field.label"
              :help="field.description"
              :required="field.required"
            >
              <template #default="{ id, describedBy, invalid }">
                <UiInput
                  :id="id"
                  :model-value="configFieldValue(provider.key, field.key)"
                  :type="inputType(field)"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  :placeholder="field.placeholder"
                  size="sm"
                  @update:model-value="setConfigField(provider.key, field.key, $event)"
                />
              </template>
            </UiFormField>
            <div class="flex flex-wrap gap-2">
              <UiButton
                size="sm"
                variant="primary"
                icon-left="save"
                :loading="isBusy(provider.key, 'save')"
                :disabled="!secretByProvider[provider.key]?.trim()"
                @click="saveCredential(provider)"
              >
                Save
              </UiButton>
              <UiButton
                size="sm"
                icon-left="plug-zap"
                :loading="isBusy(provider.key, 'test')"
                :disabled="!connectionFor(provider.key)"
                @click="testProvider(provider)"
              >
                Test
              </UiButton>
              <UiButton
                size="sm"
                variant="danger"
                icon-left="ban"
                :loading="isBusy(provider.key, 'revoke')"
                :disabled="!connectionFor(provider.key)"
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
