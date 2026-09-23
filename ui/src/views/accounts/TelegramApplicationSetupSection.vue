<script setup lang="ts">
import ConnectionCredentialField from '@/views/connections/ConnectionCredentialField.vue'
import type { AuthField } from '@/views/connections/types'

defineProps<{
  apiId: string
  apiHash: string
  errors: Record<string, string>
}>()

defineEmits<{
  (event: 'update:api-id', value: string | number | null): void
  (event: 'update:api-hash', value: string | number | null): void
}>()

const apiIdField: AuthField = {
  key: 'api_id',
  label: 'Application API ID',
  type: 'number',
  secret: false,
  required: true,
}
const apiHashField: AuthField = {
  key: 'api_hash',
  label: 'Application API hash',
  type: 'secret',
  secret: true,
  required: true,
}
</script>

<template>
  <section
    class="rounded-md border border-default bg-bg-surface p-4"
    aria-labelledby="telegram-app-title"
  >
    <div class="mb-4 space-y-1">
      <h3 id="telegram-app-title" class="text-sm font-medium text-fg-strong">
        Telegram application · one-time setup
      </h3>
      <p class="text-xs leading-5 text-fg-muted">
        TDLib uses one application for all bot and user Accounts on this device. Get its API ID and
        hash at
        <a
          href="https://my.telegram.org/apps"
          target="_blank"
          rel="noopener noreferrer"
          class="focus-ring rounded-sm text-fg-link underline underline-offset-2"
          >my.telegram.org</a
        >. You will not need to enter them for the next Account.
      </p>
    </div>
    <div class="grid gap-4">
      <ConnectionCredentialField
        :field="apiIdField"
        :model-value="apiId"
        input-type="number"
        :secret="false"
        :select="false"
        :options="[]"
        :error="errors.api_id"
        @update:model-value="$emit('update:api-id', $event)"
      />
      <ConnectionCredentialField
        :field="apiHashField"
        :model-value="apiHash"
        input-type="text"
        :secret="true"
        :select="false"
        :options="[]"
        :error="errors.api_hash"
        @update:model-value="$emit('update:api-hash', $event)"
      />
    </div>
  </section>
</template>
