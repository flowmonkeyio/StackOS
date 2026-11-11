<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaLoadedWorkflowTemplate } from '@/api'
import ScheduleInputField from './ScheduleInputField.vue'
import { workflowFields, type InputValues } from './scheduleInputs'
const props = defineProps<{ workflow: SchemaLoadedWorkflowTemplate; modelValue: InputValues; enabling: boolean; submitted: boolean; disabled?: boolean }>()
const emit = defineEmits<{ change: [key: string, value: unknown] }>()
const fields = computed(() => workflowFields(props.workflow))
</script>

<template>
  <section
    aria-label="Workflow inputs"
    class="space-y-4"
  >
    <h3 class="text-sm font-semibold text-fg-strong">
      Workflow inputs
    </h3>
    <ScheduleInputField
      v-for="field in fields"
      :key="field.key"
      :field="field"
      :model-value="modelValue[field.key]"
      :enabling="enabling"
      :submitted="submitted"
      :disabled="disabled"
      @update:model-value="emit('change', field.key, $event)"
    />
    <p
      v-if="!fields.length"
      class="text-sm text-fg-muted"
    >
      This workflow declares no editable inputs. Saved values are preserved.
    </p>
  </section>
</template>
