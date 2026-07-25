<script setup lang="ts">
import {
  UiButton,
  UiCallout,
  UiFormField,
  UiSelect,
  UiSidePanel,
} from '@/components/ui'
import type { MessageMap } from './types'

defineProps<{
  modelValue: boolean
  selectedAccountRef: string
  accountOptions: Array<{ value: string; label: string; group?: string }>
  message: MessageMap[string] | null
  busy: boolean
}>()

defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'update:selectedAccountRef', value: string | number | null): void
  (event: 'attach'): void
  (event: 'create-account'): void
}>()
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    title="Add connection"
    description="Choose an Account not yet attached to this project."
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div class="grid gap-5">
      <UiCallout
        v-if="accountOptions.length === 0"
        tone="info"
      >
        No eligible Accounts are available for this project. Create another Account, or choose a
        different provider.
      </UiCallout>

      <UiCallout
        v-if="message"
        :tone="message.tone"
        density="compact"
      >
        {{ message.text }}
      </UiCallout>

      <UiFormField
        v-if="accountOptions.length > 0"
        label="Accounts not yet attached"
        help="Choose one Account. Other Accounts from the same service can also be attached."
        required
      >
        <UiSelect
          :model-value="selectedAccountRef || null"
          :options="accountOptions"
          placeholder="Select an Account"
          searchable
          search-placeholder="Search Accounts"
          empty-label="No Accounts match your search"
          @update:model-value="$emit('update:selectedAccountRef', $event)"
        />
      </UiFormField>
    </div>

    <template #footer>
      <UiButton
        class="mr-auto"
        size="sm"
        variant="secondary"
        icon-left="plus"
        @click="$emit('create-account')"
      >
        Create another Account
      </UiButton>
      <UiButton
        variant="ghost"
        @click="$emit('update:modelValue', false)"
      >
        Cancel
      </UiButton>
      <UiButton
        variant="primary"
        :disabled="!selectedAccountRef"
        :loading="busy"
        @click="$emit('attach')"
      >
        Attach Account
      </UiButton>
    </template>
  </UiSidePanel>
</template>
