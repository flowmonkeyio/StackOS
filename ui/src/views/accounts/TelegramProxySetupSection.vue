<script setup lang="ts">
import { computed, ref, useId } from 'vue'

import { UiIcon } from '@/components/ui'
import ConnectionCredentialField from '@/views/connections/ConnectionCredentialField.vue'
import type { AuthField } from '@/views/connections/types'

type FieldUpdate = { fieldKey: string; value: string | number | null }

const props = defineProps<{
  fields: AuthField[]
  fieldValues: Record<string, string>
  fieldErrors: Record<string, string>
  editing: boolean
  secretPresent: Record<string, boolean>
  inputType: (field: AuthField) => 'text' | 'url' | 'number' | 'email'
  isSecretField: (field: AuthField) => boolean
  hasFieldOptions: (field: AuthField) => boolean
  fieldOptions: (field: AuthField) => Array<{ value: string; label: string }>
}>()

const emit = defineEmits<{
  (event: 'update:field', update: FieldUpdate): void
}>()

const detailsId = useId()
const advancedId = useId()
const enabled = computed(() => props.fieldValues.proxy_enabled === 'true')
const selectedType = computed(() => props.fieldValues.proxy_type ?? '')
const expanded = ref(enabled.value)
const advancedExpanded = ref(false)

const fieldByKey = computed(() => new Map(props.fields.map((field) => [field.key, field])))
const enabledField = computed(() => fieldByKey.value.get('proxy_enabled'))
const coreFields = computed(() =>
  ['proxy_type', 'proxy_host', 'proxy_port']
    .map((key) => fieldByKey.value.get(key))
    .filter((field): field is AuthField => Boolean(field)),
)
const protocolFields = computed(() => {
  const keys = selectedType.value === 'mtproto'
    ? ['proxy_secret']
    : selectedType.value === 'http'
      ? ['proxy_username', 'proxy_password', 'proxy_http_only']
      : selectedType.value === 'socks5'
        ? ['proxy_username', 'proxy_password']
        : []
  return keys
    .map((key) => fieldByKey.value.get(key))
    .filter((field): field is AuthField => Boolean(field))
})
const advancedFields = computed(() =>
  selectedType.value === 'mtproto' ? [] : protocolFields.value,
)
const hasProxyErrors = computed(() => {
  const visibleKeys = enabled.value
    ? ['proxy_enabled', ...coreFields.value.map((field) => field.key), ...protocolFields.value.map((field) => field.key)]
    : ['proxy_enabled']
  return visibleKeys.some((key) => Boolean(props.fieldErrors[key]))
})
const detailsVisible = computed(() => expanded.value || hasProxyErrors.value)
const advancedVisible = computed(() =>
  advancedExpanded.value || advancedFields.value.some((field) => Boolean(props.fieldErrors[field.key])),
)
const proxySummary = computed(() => {
  if (!enabled.value) return 'Direct connection'
  const typeField = fieldByKey.value.get('proxy_type')
  const label = typeField
    ? props.fieldOptions(typeField).find((option) => option.value === selectedType.value)?.label
    : undefined
  return label ? `${label} proxy` : 'Proxy enabled · complete settings'
})

function emitField(fieldKey: string, value: string | number | null): void {
  emit('update:field', { fieldKey, value })
}

function clearField(fieldKey: string, clearedValue = ''): void {
  const value = props.fieldValues[fieldKey]
  if (!value || value === clearedValue) return
  emitField(fieldKey, clearedValue)
}

function updateEnabled(value: string | number | null): void {
  emitField('proxy_enabled', value)
  if (value === 'true') {
    expanded.value = true
    return
  }
  advancedExpanded.value = false
  for (const key of [
    'proxy_type',
    'proxy_host',
    'proxy_port',
    'proxy_http_only',
    'proxy_username',
    'proxy_password',
    'proxy_secret',
  ]) clearField(key)
}

function updateType(value: string | number | null): void {
  expanded.value = true
  emitField('proxy_type', value)
  // SOCKS5 needs an explicit false to replace a saved HTTP-only setting. MTProto
  // needs the field omitted so provider validation can remove it entirely.
  if (value === 'socks5') clearField('proxy_http_only', 'false')
  else if (value !== 'http') clearField('proxy_http_only')
  if (value === 'mtproto') {
    clearField('proxy_username')
    clearField('proxy_password')
  } else {
    clearField('proxy_secret')
  }
}

function updateField(fieldKey: string, value: string | number | null): void {
  if (fieldKey === 'proxy_enabled') updateEnabled(value)
  else if (fieldKey === 'proxy_type') updateType(value)
  else emitField(fieldKey, value)
}
</script>

<template>
  <section
    v-if="enabledField"
    class="rounded-md border border-default bg-bg-surface"
  >
    <button
      type="button"
      class="flex w-full items-center justify-between gap-3 rounded-md px-4 py-3 text-left hover:bg-bg-surface-alt focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
      :aria-expanded="detailsVisible"
      :aria-controls="detailsId"
      @click="expanded = !detailsVisible"
    >
      <span class="min-w-0">
        <span class="block text-sm font-medium text-fg-default">Proxy settings (optional)</span>
        <span class="mt-0.5 block truncate text-xs text-fg-muted">{{ proxySummary }}</span>
      </span>
      <UiIcon
        name="chevron-down"
        class="h-4 w-4 text-fg-muted"
        :class="detailsVisible ? 'rotate-180' : ''"
      />
    </button>

    <div
      v-if="detailsVisible"
      :id="detailsId"
      class="grid gap-4 border-t border-subtle px-4 py-4"
    >
      <ConnectionCredentialField
        :field="enabledField"
        :model-value="fieldValues.proxy_enabled ?? ''"
        :input-type="inputType(enabledField)"
        :secret="isSecretField(enabledField)"
        :select="hasFieldOptions(enabledField)"
        :options="fieldOptions(enabledField)"
        :error="fieldErrors.proxy_enabled"
        :editing="editing"
        :secret-present="secretPresent.proxy_enabled ?? false"
        @update:model-value="updateField(enabledField.key, $event)"
      />

      <template v-if="enabled">
        <ConnectionCredentialField
          v-for="field in coreFields"
          :key="field.key"
          :field="field"
          :model-value="fieldValues[field.key] ?? ''"
          :input-type="inputType(field)"
          :secret="isSecretField(field)"
          :select="hasFieldOptions(field)"
          :options="fieldOptions(field)"
          :error="fieldErrors[field.key]"
          :editing="editing"
          :secret-present="secretPresent[field.key] ?? false"
          @update:model-value="updateField(field.key, $event)"
        />

        <ConnectionCredentialField
          v-if="selectedType === 'mtproto' && protocolFields[0]"
          :field="protocolFields[0]"
          :model-value="fieldValues.proxy_secret ?? ''"
          :input-type="inputType(protocolFields[0])"
          :secret="isSecretField(protocolFields[0])"
          :select="hasFieldOptions(protocolFields[0])"
          :options="fieldOptions(protocolFields[0])"
          :error="fieldErrors.proxy_secret"
          :editing="editing"
          :secret-present="secretPresent.proxy_secret ?? false"
          @update:model-value="updateField('proxy_secret', $event)"
        />

        <div
          v-if="advancedFields.length"
          class="border-t border-subtle pt-3"
        >
          <button
            type="button"
            class="flex w-full items-center justify-between gap-2 rounded-sm py-1 text-left text-xs font-medium text-fg-default hover:text-fg-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            :aria-label="advancedVisible ? 'Hide advanced proxy options' : 'Show advanced proxy options'"
            :aria-expanded="advancedVisible"
            :aria-controls="advancedId"
            @click="advancedExpanded = !advancedVisible"
          >
            <span>Advanced proxy options</span>
            <UiIcon
              name="chevron-down"
              class="h-4 w-4 text-fg-muted"
              :class="advancedVisible ? 'rotate-180' : ''"
            />
          </button>
          <div
            v-if="advancedVisible"
            :id="advancedId"
            class="mt-3 grid gap-4"
          >
            <ConnectionCredentialField
              v-for="field in advancedFields"
              :key="field.key"
              :field="field"
              :model-value="fieldValues[field.key] ?? ''"
              :input-type="inputType(field)"
              :secret="isSecretField(field)"
              :select="hasFieldOptions(field)"
              :options="fieldOptions(field)"
              :error="fieldErrors[field.key]"
              :editing="editing"
              :secret-present="secretPresent[field.key] ?? false"
              @update:model-value="updateField(field.key, $event)"
            />
          </div>
        </div>
      </template>
    </div>
  </section>
</template>
