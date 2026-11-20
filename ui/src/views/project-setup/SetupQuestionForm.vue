<script setup lang="ts">
import { nextTick, onMounted, onUpdated, ref } from 'vue'
import type { SchemaSetupAttempt } from '@/api'
import { UiButton, UiFormField, UiTextarea } from '@/components/ui'

const props = defineProps<{
  attempt: SchemaSetupAttempt
  pending: boolean
  disabled: boolean
  submit: (snapshot: SchemaSetupAttempt, answer: string) => Promise<boolean>
}>()
const draft = ref('')
const form = ref<HTMLFormElement | null>(null)
let focused = false
function focusQuestion(): void {
  if (focused || props.disabled || props.pending) return
  form.value?.querySelector('textarea')?.focus({ preventScroll: true })
  focused = true
}
onMounted(focusQuestion)
onUpdated(focusQuestion)
async function send(): Promise<void> {
  if (props.pending || props.disabled || !draft.value.trim() || draft.value.length > 8000) return
  if (!await props.submit(props.attempt, draft.value)) {
    await nextTick()
    form.value?.querySelector('textarea')?.focus({ preventScroll: true })
  }
}
</script>

<template>
  <form
    ref="form"
    class="space-y-3"
    @submit.prevent="send"
    @keydown.ctrl.enter.prevent="send"
    @keydown.meta.enter.prevent="send"
  >
    <UiFormField
      label="Your answer"
      help="Send with the button or Ctrl/⌘ + Enter."
      required
    >
      <UiTextarea
        v-model="draft"
        :disabled="pending || disabled"
        :rows="4"
        :maxlength="8000"
        resize="vertical"
      />
    </UiFormField>
    <div class="flex justify-end">
      <UiButton
        type="submit"
        variant="primary"
        :loading="pending"
        :disabled="disabled || !draft.trim() || draft.length > 8000"
      >
        Send answer
      </UiButton>
    </div>
  </form>
</template>
