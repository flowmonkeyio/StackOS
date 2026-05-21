<!--
  UiConfirmDialog — convenience wrapper around UiDialog for yes/no questions.

  <UiConfirmDialog
    v-model="open"
    title="Delete resource?"
    description="This removes the selected record and its attached artifacts."
    confirm-label="Delete"
    tone="danger"
    @confirm="onConfirm"
  />
-->
<script setup lang="ts">
import UiDialog from './UiDialog.vue';
import UiButton from './UiButton.vue';

defineProps<{
  modelValue: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: 'primary' | 'danger';
  loading?: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'confirm'): void;
  (e: 'cancel'): void;
}>();

function close() { emit('update:modelValue', false); }
function cancel() { emit('cancel'); close(); }
function confirm() { emit('confirm'); }
</script>

<template>
  <UiDialog
    :model-value="modelValue"
    :title="title"
    :description="description"
    size="sm"
    @update:model-value="(v) => $emit('update:modelValue', v)"
  >
    <slot />
    <template #footer>
      <UiButton variant="ghost" :disabled="loading" @click="cancel">{{ cancelLabel ?? 'Cancel' }}</UiButton>
      <UiButton
        :variant="tone === 'danger' ? 'danger' : 'primary'"
        :loading="loading"
        @click="confirm"
      >{{ confirmLabel ?? 'Confirm' }}</UiButton>
    </template>
  </UiDialog>
</template>
