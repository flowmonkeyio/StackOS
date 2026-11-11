<script setup lang="ts">
import { computed, useId } from 'vue'
import { UiButton, UiCheckbox, UiFormField, UiInput, UiSelect, UiTextarea } from '@/components/ui'
import { fieldError, fieldKind, fieldOptions, isObject, itemField, propertyFields, valueSummary, type InputField } from './scheduleInputs'

const props = defineProps<{ field: InputField; modelValue?: unknown; enabling: boolean; submitted: boolean; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: unknown] }>()
const id = useId()
const kind = computed(() => fieldKind(props.field))
const error = computed(() => props.submitted ? fieldError(props.field, props.modelValue, props.enabling) : null)
const options = computed(() => fieldOptions(props.field))
const selection = computed(() => {
  const index = options.value.findIndex(option => JSON.stringify(option) === JSON.stringify(props.modelValue))
  return index < 0 ? '' : String(index)
})
const list = computed(() => Array.isArray(props.modelValue) ? props.modelValue : [])
const incompatible = computed(() => props.modelValue != null && (
  (kind.value === 'object' && !isObject(props.modelValue)) ||
  (kind.value === 'array' && !Array.isArray(props.modelValue)) ||
  (['string', 'text', 'multiline', 'number', 'integer', 'boolean'].includes(kind.value) && typeof props.modelValue === 'object')
))
function property(key: string, value: unknown) {
  emit('update:modelValue', { ...(isObject(props.modelValue) ? props.modelValue : {}), [key]: value })
}
function item(index: number, value: unknown) { emit('update:modelValue', list.value.map((old, i) => i === index ? value : old)) }
function add() {
  const field = itemField(props.field, list.value.length)
  const type = fieldKind(field)
  const value = field.schema.default ?? (type === 'object' ? {} : type === 'array' ? [] : type === 'boolean' ? false : type === 'option' ? fieldOptions(field)[0] : type === 'number' || type === 'integer' ? null : '')
  emit('update:modelValue', [...list.value, value])
}
</script>

<template>
  <UiFormField
    :input-id="id"
    :label="field.label"
    :help="field.help"
    :required="field.required"
    show-optional
    :error="error"
  >
    <p
      v-if="kind === 'opaque' || incompatible"
      :id="id"
      class="text-sm text-fg-muted"
    >
      {{ valueSummary(modelValue) }}. {{ modelValue == null ? (field.required ? 'Complete workflow setup before enabling. Disabled drafts can be saved.' : 'Optional context can be inherited from workflow setup.') : 'This value is preserved. Changes to this structure belong in workflow setup.' }}
    </p>
    <UiSelect
      v-else-if="kind === 'option'"
      :model-value="selection"
      :options="[{ value: '', label: modelValue == null ? 'Choose an option' : 'Saved value is outside the declared options' }, ...options.map((value, index) => ({ value: String(index), label: valueSummary(value) }))]"
      :disabled="disabled"
      @update:model-value="emit('update:modelValue', $event === '' || $event == null ? null : options[Number($event)])"
    />
    <UiCheckbox
      v-else-if="kind === 'boolean'"
      :id="id"
      :model-value="typeof modelValue === 'boolean' ? modelValue : null"
      :label="field.label"
      :aria-describedby="error ? `${id}-err` : field.help ? `${id}-help` : undefined"
      :invalid="!!error"
      :disabled="disabled"
      @update:model-value="emit('update:modelValue', $event)"
    />
    <div
      v-else-if="kind === 'object'"
      :id="id"
      role="group"
      :aria-label="field.label"
      class="space-y-3 border-l border-subtle pl-3"
    >
      <ScheduleInputField
        v-for="child in propertyFields(field)"
        :key="child.key"
        :field="child"
        :model-value="isObject(modelValue) ? modelValue[child.key] : undefined"
        :enabling="enabling && (modelValue != null || field.required)"
        :submitted="submitted"
        :disabled="disabled"
        @update:model-value="property(child.key, $event)"
      />
    </div>
    <div
      v-else-if="kind === 'array'"
      :id="id"
      role="group"
      :aria-label="field.label"
      class="space-y-3"
    >
      <div
        v-for="(value, index) in list.slice(0, 100)"
        :key="index"
        class="space-y-2 border-l border-subtle pl-3"
      >
        <ScheduleInputField
          :field="itemField(field, index)"
          :model-value="value"
          :enabling="enabling"
          :submitted="submitted"
          :disabled="disabled"
          @update:model-value="item(index, $event)"
        />
        <UiButton
          :disabled="disabled"
          @click="emit('update:modelValue', list.filter((_, i) => i !== index))"
        >
          Remove item {{ index + 1 }}
        </UiButton>
      </div>
      <p
        v-if="list.length > 100"
        class="text-xs text-fg-muted"
      >
        Remaining {{ list.length - 100 }} items are preserved. Manage large lists in workflow setup.
      </p>
      <UiButton
        :disabled="disabled || list.length >= Math.min(100, typeof field.schema.maxItems === 'number' ? field.schema.maxItems : 100)"
        @click="add"
      >
        Add item
      </UiButton>
    </div>
    <UiTextarea
      v-else-if="kind === 'multiline' || field.schema.format === 'multiline' || field.schema.format === 'textarea'"
      :model-value="typeof modelValue === 'string' ? modelValue : ''"
      :rows="3"
      :disabled="disabled"
      @update:model-value="emit('update:modelValue', $event)"
    />
    <UiInput
      v-else
      :type="kind === 'number' || kind === 'integer' ? 'number' : 'text'"
      :model-value="typeof modelValue === 'string' || typeof modelValue === 'number' ? modelValue : null"
      :step="kind === 'integer' ? 1 : 'any'"
      :disabled="disabled"
      @update:model-value="emit('update:modelValue', $event)"
    />
  </UiFormField>
</template>
