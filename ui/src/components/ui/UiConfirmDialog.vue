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
<script lang="ts">
let confirmIdSeed = 0;
</script>

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

const descriptionId = `ui-confirm-desc-${++confirmIdSeed}`;

function close() { emit('update:modelValue', false); }
function cancel() { emit('cancel'); close(); }
function confirm() { emit('confirm'); }
</script>

<template>
  <UiDialog
    :model-value="modelValue"
    :title="title"
    :described-by="description ? descriptionId : undefined"
    size="sm"
    @update:model-value="(v) => $emit('update:modelValue', v)"
  >
    <div class="space-y-3">
      <p
        v-if="description"
        :id="descriptionId"
        class="text-sm text-fg-default break-words"
      >
        {{ description }}
      </p>
      <slot />
    </div>
    <template #footer>
      <UiButton
        variant="ghost"
        :disabled="loading"
        @click="cancel"
      >
        {{ cancelLabel ?? 'Cancel' }}
      </UiButton>
      <UiButton
        :variant="tone === 'danger' ? 'danger' : 'primary'"
        :loading="loading"
        @click="confirm"
      >
        {{ confirmLabel ?? 'Confirm' }}
      </UiButton>
    </template>
  </UiDialog>
</template>
