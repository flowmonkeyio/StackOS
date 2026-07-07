<!--
  UiSecretInput — masked input for API keys, tokens, passwords.
  Adds reveal toggle, copy button, and obscured display.
-->
<script setup lang="ts">
import { computed, ref } from 'vue';
import UiIcon from './UiIcon.vue';
import UiIconButton from './UiIconButton.vue';
import UiInput from './UiInput.vue';
import { copyTextToClipboard } from '@/lib/clipboard';

export interface UiSecretInputProps {
  modelValue?: string;
  placeholder?: string;
  disabled?: boolean;
  invalid?: boolean;
  required?: boolean;
  /** Hide reveal toggle (e.g. for write-only fields). */
  noReveal?: boolean;
  /** Hide copy button. */
  noCopy?: boolean;
  /** Show only last N chars when masked. */
  maskedSuffixLen?: number;
  size?: 'sm' | 'md' | 'lg';
  id?: string;
  ariaDescribedby?: string;
}

const props = withDefaults(defineProps<UiSecretInputProps>(), {
  modelValue: '',
  placeholder: undefined,
  size: 'md',
  maskedSuffixLen: 4,
  id: undefined,
  ariaDescribedby: undefined,
});

defineEmits<{ (e: 'update:modelValue', v: string): void }>();

const revealed = ref(false);
const copied = ref(false);

const displayType = computed(() => (revealed.value ? 'text' : 'password'));

async function copy() {
  if (!props.modelValue) return;
  if (await copyTextToClipboard(props.modelValue)) {
    copied.value = true;
    setTimeout(() => { copied.value = false; }, 1500);
  }
}
</script>

<template>
  <UiInput
    :id="id"
    :model-value="modelValue"
    :type="displayType"
    :placeholder="placeholder"
    :disabled="disabled"
    :invalid="invalid"
    :required="required"
    :size="size"
    :aria-describedby="ariaDescribedby"
    autocomplete="off"
    spellcheck="false"
    @update:model-value="(v: any) => $emit('update:modelValue', String(v ?? ''))"
  >
    <template #suffix>
      <div class="flex items-center gap-0.5">
        <UiIconButton
          v-if="!noReveal"
          :aria-label="revealed ? 'Hide value' : 'Reveal value'"
          size="sm"
          variant="ghost"
          @click="revealed = !revealed"
        >
          <UiIcon :name="revealed ? 'eye-off' : 'eye'" />
        </UiIconButton>
        <UiIconButton
          v-if="!noCopy"
          :aria-label="copied ? 'Copied' : 'Copy value'"
          size="sm"
          variant="ghost"
          @click="copy"
        >
          <UiIcon :name="copied ? 'check' : 'copy'" />
        </UiIconButton>
      </div>
    </template>
  </UiInput>
</template>
