<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { UiButton, UiDialog, UiFormField, UiInput } from '@/components/ui'
import { useProjectsStore, type Project } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'

const props = defineProps<{ project: Project }>()
const emit = defineEmits<{ close: [] }>()
const projects = useProjectsStore()
const toasts = useToastsStore()
// The app keys this dialog by target ID; reopening starts from the shared name.
const draft = ref(props.project.name)
const saving = ref(false)
const failure = ref<string | null>(null)
const form = ref<HTMLFormElement | null>(null)
onMounted(async () => {
  await nextTick()
  form.value?.querySelector('input')?.focus()
})
const validation = computed(() => {
  if (!draft.value.trim()) return 'Enter a project name.'
  if (Array.from(draft.value.trim()).length > 200) return 'Use 200 characters or fewer.'
  return null
})
const canSave = computed(() => !validation.value && draft.value.trim() !== props.project.name && !saving.value)

function close(): void {
  if (!saving.value) emit('close')
}
async function save(): Promise<void> {
  if (!canSave.value) return
  saving.value = true
  failure.value = null
  try {
    await projects.renameProject(props.project.id, draft.value)
    toasts.success('Project renamed')
    emit('close')
  } catch {
    failure.value = "Couldn't save the name. Try again."
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <UiDialog
    :model-value="true"
    title="Rename project"
    :static-backdrop="saving"
    :no-escape="saving"
    :hide-close="saving"
    @update:model-value="close"
  >
    <form
      id="rename-project-form"
      ref="form"
      @submit.prevent="save"
    >
      <UiFormField
        label="Project name"
        :help="validation || failure ? undefined : 'Only the name shown in StackOS changes.'"
        :error="validation ?? failure"
      >
        <UiInput
          v-model="draft"
          :disabled="saving"
          data-autofocus
          @focus="($event.target as HTMLInputElement).select()"
          @update:model-value="failure = null"
        />
      </UiFormField>
    </form>
    <template #footer>
      <UiButton
        variant="ghost"
        :disabled="saving"
        @click="close"
      >
        Cancel
      </UiButton>
      <UiButton
        type="submit"
        form="rename-project-form"
        variant="primary"
        :disabled="!canSave"
        :loading="saving"
      >
        {{ saving ? 'Saving…' : 'Save name' }}
      </UiButton>
    </template>
  </UiDialog>
</template>
