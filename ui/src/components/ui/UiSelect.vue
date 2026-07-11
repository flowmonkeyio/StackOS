<!--
  UiSelect — custom listbox select styled to match UiInput.

  Use for short, bounded option sets. Options can be strings or
  { value, label, disabled?, group?, rightLabel?, rightMeta? } objects.
-->
<script setup lang="ts">
import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  useAttrs,
  useId,
} from 'vue'
import type { ComputedRef } from 'vue'

import UiIcon from './UiIcon.vue'
import UiInput from './UiInput.vue'

defineOptions({ inheritAttrs: false })

export type UiSelectTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent'

export type UiSelectOption =
  | string
  | {
      value: string | number
      label: string
      disabled?: boolean
      group?: string
      rightLabel?: string
      rightMeta?: string
      rightTone?: UiSelectTone
    }

export interface UiSelectProps {
  modelValue?: string | number | null
  options: UiSelectOption[]
  size?: 'sm' | 'md' | 'lg'
  placeholder?: string
  disabled?: boolean
  invalid?: boolean
  required?: boolean
  block?: boolean
  id?: string
  ariaDescribedby?: string
  searchable?: boolean
  searchPlaceholder?: string
  emptyLabel?: string
}

interface NormalizedOption {
  value: string | number
  label: string
  disabled?: boolean
  group?: string
  rightLabel?: string
  rightMeta?: string
  rightTone?: UiSelectTone
}

const props = withDefaults(defineProps<UiSelectProps>(), {
  modelValue: null,
  size: 'md',
  placeholder: undefined,
  block: true,
  id: undefined,
  ariaDescribedby: undefined,
  searchable: false,
  searchPlaceholder: 'Search',
  emptyLabel: 'No options found',
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | number | null): void
  (e: 'change', value: string | number | null): void
}>()

const attrs = useAttrs()
const field = inject<{
  id: ComputedRef<string>
  describedBy: ComputedRef<string | undefined>
  invalid: ComputedRef<boolean>
  required: ComputedRef<boolean>
} | null>('uiFormField', null)
const autoId = useId()
const rootRef = ref<HTMLDivElement | null>(null)
const buttonRef = ref<HTMLButtonElement | null>(null)
const open = ref(false)
const activeIndex = ref(0)
const searchQuery = ref('')

const controlId = computed(() => props.id ?? field?.id.value ?? `select-${autoId}`)
const listboxId = computed(() => `${controlId.value}-listbox`)
const searchInputId = computed(() => `${controlId.value}-search`)
const controlDescribedBy = computed(() => props.ariaDescribedby ?? field?.describedBy.value)
const controlInvalid = computed(() => props.invalid || field?.invalid.value)
const controlRequired = computed(() => props.required || field?.required.value)

const normalized = computed<NormalizedOption[]>(() =>
  props.options.map((option) =>
    typeof option === 'string' ? { value: option, label: option } : option,
  ),
)

const filteredNormalized = computed<NormalizedOption[]>(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return normalized.value
  return normalized.value.filter((option) => optionSearchText(option).includes(query))
})

const grouped = computed(() => {
  const groups = new Map<string | null, NormalizedOption[]>()
  for (const option of filteredNormalized.value) {
    const key = option.group ?? null
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(option)
  }
  return Array.from(groups.entries())
})

const selectedOption = computed(() =>
  normalized.value.find((option) => String(option.value) === String(props.modelValue ?? '')),
)

const selectedLabel = computed(() => selectedOption.value?.label ?? props.placeholder ?? 'Select')
const selectedIndex = computed(() =>
  normalized.value.findIndex((option) => String(option.value) === String(props.modelValue ?? '')),
)
const activeOptionId = computed(() => {
  if (!open.value) return undefined
  const option = normalized.value[activeIndex.value]
  if (!option || option.disabled || !filteredNormalized.value.includes(option)) return undefined
  return optionId(option)
})

const sizeClass = computed(
  () =>
    ({
      sm: 'h-7 text-sm pl-2 pr-8',
      md: 'h-8 text-sm pl-2.5 pr-8',
      lg: 'h-10 text-base pl-3 pr-9',
    })[props.size],
)

const RIGHT_TONE_CLASSES: Record<UiSelectTone, string> = {
  neutral: 'bg-neutral-subtle text-neutral-fg',
  info: 'bg-info-subtle text-info-fg',
  success: 'bg-success-subtle text-success-fg',
  warning: 'bg-warning-subtle text-warning-fg',
  danger: 'bg-danger-subtle text-danger-fg',
  accent: 'bg-accent-subtle text-accent-fg',
}

const RIGHT_CHIP_CLASS =
  'ui-select__right-chip inline-block min-w-0 max-w-[8.5rem] truncate rounded-xs px-1.5 py-0.5 text-2xs font-medium leading-none'

const RIGHT_META_CLASS =
  'ui-select__right-meta inline-block min-w-0 max-w-[8.5rem] truncate font-mono text-2xs font-medium leading-none'

function hasRightContent(option: NormalizedOption | undefined): boolean {
  return Boolean(option?.rightLabel || option?.rightMeta)
}

function rightToneClass(option: NormalizedOption): string {
  return RIGHT_TONE_CLASSES[option.rightTone ?? 'neutral']
}

function isSelected(option: NormalizedOption): boolean {
  return String(option.value) === String(props.modelValue ?? '')
}

function optionSearchText(option: NormalizedOption): string {
  return [option.label, option.value, option.group, option.rightLabel, option.rightMeta]
    .filter((part) => part !== undefined && part !== null)
    .join(' ')
    .toLowerCase()
}

function visibleIndexFor(option: NormalizedOption): number {
  return normalized.value.findIndex((candidate) => candidate.value === option.value)
}

function optionId(option: NormalizedOption): string {
  return `${listboxId.value}-option-${visibleIndexFor(option)}`
}

function visibleActiveOptions(): NormalizedOption[] {
  return filteredNormalized.value.filter((option) => !option.disabled)
}

function firstEnabledIndex(): number {
  const firstVisible = visibleActiveOptions()[0]
  return firstVisible ? visibleIndexFor(firstVisible) : 0
}

function setActiveFromSelection(): void {
  const selected = selectedIndex.value
  const selectedVisible =
    selected >= 0 &&
    !normalized.value[selected]?.disabled &&
    filteredNormalized.value.some((option) => option.value === normalized.value[selected]?.value)
  activeIndex.value = selectedVisible ? selected : firstEnabledIndex()
}

function focusSearchInput(): void {
  const input = rootRef.value?.querySelector<HTMLInputElement>('input[aria-label="Search options"]')
  input?.focus()
}

function setOpen(next: boolean): void {
  if (props.disabled) return
  open.value = next
  if (next) {
    searchQuery.value = ''
    setActiveFromSelection()
    if (props.searchable) void nextTick(focusSearchInput)
  }
}

function moveActive(direction: 1 | -1): void {
  const options = visibleActiveOptions()
  if (options.length === 0) return
  const currentVisibleIndex = options.findIndex((option) => visibleIndexFor(option) === activeIndex.value)
  const current = currentVisibleIndex >= 0 ? currentVisibleIndex : 0
  const next = (current + direction + options.length) % options.length
  activeIndex.value = visibleIndexFor(options[next])
}

function selectOption(option: NormalizedOption): void {
  if (option.disabled) return
  emit('update:modelValue', option.value)
  emit('change', option.value)
  open.value = false
  void nextTick(() => buttonRef.value?.focus())
}

function setSearchQuery(value: string | number | null): void {
  searchQuery.value = String(value ?? '')
  if (open.value) setActiveFromSelection()
}

function selectActiveOption(): void {
  const option =
    filteredNormalized.value.find((candidate) => visibleIndexFor(candidate) === activeIndex.value) ??
    filteredNormalized.value.find((candidate) => !candidate.disabled)
  if (option) selectOption(option)
}

function onButtonKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (!open.value) setOpen(true)
    else moveActive(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (!open.value) setOpen(true)
    else moveActive(-1)
  } else if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
    event.preventDefault()
    if (!open.value) setOpen(true)
    else selectActiveOption()
  } else if (event.key === 'Escape') {
    open.value = false
  }
}

function onSearchKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    moveActive(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(-1)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    selectActiveOption()
  } else if (event.key === 'Escape') {
    open.value = false
    void nextTick(() => buttonRef.value?.focus())
  } else if (event.key === 'Tab') {
    open.value = false
  }
}

function onPointerDown(event: PointerEvent): void {
  if (!open.value) return
  const root = rootRef.value
  if (root && !root.contains(event.target as Node)) open.value = false
}

onMounted(() => document.addEventListener('pointerdown', onPointerDown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onPointerDown))
</script>

<template>
  <div
    ref="rootRef"
    :class="['ui-select relative inline-block', block && 'w-full']"
  >
    <button
      v-bind="attrs"
      :id="controlId"
      ref="buttonRef"
      type="button"
      role="combobox"
      :aria-expanded="open"
      :aria-controls="listboxId"
      :aria-activedescendant="!searchable ? activeOptionId : undefined"
      :aria-invalid="controlInvalid || undefined"
      :aria-describedby="controlDescribedBy"
      :aria-required="controlRequired || undefined"
      :disabled="disabled"
      :class="[
        'ui-select__button focus-ring flex w-full items-center rounded-sm border bg-bg-surface text-left text-fg-default shadow-xs transition-colors duration-fast',
        controlInvalid
          ? 'border-danger'
          : open
            ? 'border-accent'
            : 'border-default hover:border-strong hover:bg-bg-surface-alt',
        disabled &&
          'cursor-not-allowed bg-bg-sunken text-fg-disabled hover:border-default hover:bg-bg-sunken',
        sizeClass,
      ]"
      @click="setOpen(!open)"
      @keydown="onButtonKeydown"
    >
      <span
        class="min-w-0 flex-1 truncate"
        :class="!selectedOption && 'text-fg-disabled'"
      >
        <slot
          name="selected"
          :option="selectedOption"
          :label="selectedLabel"
        >
          {{ selectedLabel }}
        </slot>
      </span>
      <span
        v-if="hasRightContent(selectedOption)"
        class="pointer-events-none ml-2 flex min-w-0 max-w-[50%] shrink-0 items-center gap-1.5 overflow-hidden pr-3"
      >
        <span
          v-if="selectedOption?.rightLabel"
          :class="[RIGHT_CHIP_CLASS, rightToneClass(selectedOption)]"
        >
          {{ selectedOption.rightLabel }}
        </span>
        <span
          v-if="selectedOption?.rightMeta"
          :class="[RIGHT_META_CLASS, 'text-fg-muted']"
        >
          {{ selectedOption.rightMeta }}
        </span>
      </span>
      <span
        class="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2"
        :class="disabled ? 'text-fg-disabled' : 'text-fg-subtle'"
        aria-hidden="true"
      >
        <UiIcon
          name="chevron-up-down"
          class="ui-select__icon"
        />
      </span>
    </button>

    <div
      v-if="open"
      class="absolute left-0 right-0 top-[calc(100%+4px)] z-popover max-h-72 overflow-x-hidden overflow-y-auto rounded-lg border border-default bg-bg-surface p-1 shadow-md"
    >
      <div
        v-if="searchable"
        class="sticky top-0 z-10 -m-1 mb-1 border-b border-subtle bg-bg-surface p-1.5"
      >
        <UiInput
          :id="searchInputId"
          :model-value="searchQuery"
          type="search"
          size="sm"
          :placeholder="searchPlaceholder"
          aria-label="Search options"
          role="combobox"
          aria-autocomplete="list"
          :aria-expanded="open"
          :aria-controls="listboxId"
          :aria-activedescendant="activeOptionId"
          clearable
          @update:model-value="setSearchQuery"
          @keydown="onSearchKeydown"
        >
          <template #prefix>
            <UiIcon
              name="search"
              class="ui-select__icon"
            />
          </template>
        </UiInput>
      </div>
      <div
        :id="listboxId"
        role="listbox"
        class="min-w-0"
        :aria-labelledby="controlId"
      >
        <template
          v-for="[group, groupOptions] in grouped"
          :key="group ?? '_none'"
        >
          <div
            v-if="group"
            class="truncate px-2 py-1.5 text-2xs font-semibold uppercase tracking-wide text-fg-subtle"
          >
            {{ group }}
          </div>
          <button
            v-for="option in groupOptions"
            :id="optionId(option)"
            :key="option.value"
            type="button"
            role="option"
            tabindex="-1"
            :aria-selected="isSelected(option)"
            :disabled="option.disabled"
            :class="[
              'focus-ring-inset flex h-8 w-full min-w-0 items-center justify-between gap-2 overflow-hidden rounded-sm px-2 text-left text-sm transition-colors duration-fast',
              option.disabled
                ? 'cursor-not-allowed text-fg-disabled'
                : isSelected(option)
                  ? 'bg-accent-subtle text-accent-fg'
                  : normalized[activeIndex]?.value === option.value
                    ? 'bg-bg-surface-alt text-fg-strong'
                    : 'text-fg-default hover:bg-bg-surface-alt',
            ]"
            @mouseenter="activeIndex = visibleIndexFor(option)"
            @click="selectOption(option)"
          >
            <span class="block min-w-0 flex-1 truncate">
              <slot
                name="option"
                :option="option"
                :selected="isSelected(option)"
              >
                {{ option.label }}
              </slot>
            </span>
            <span class="ml-auto flex min-w-0 max-w-[50%] shrink-0 items-center gap-1.5 overflow-hidden">
              <span
                v-if="option.rightLabel"
                :class="[RIGHT_CHIP_CLASS, rightToneClass(option)]"
              >
                {{ option.rightLabel }}
              </span>
              <span
                v-if="option.rightMeta"
                :class="[RIGHT_META_CLASS, isSelected(option) ? 'text-accent-fg' : 'text-fg-muted']"
              >
                {{ option.rightMeta }}
              </span>
              <UiIcon
                v-if="isSelected(option)"
                name="check"
                class="ui-select__icon shrink-0"
              />
            </span>
          </button>
        </template>
        <p
          v-if="filteredNormalized.length === 0"
          class="px-2 py-3 text-sm text-fg-muted"
        >
          {{ emptyLabel }}
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ui-select__icon {
  width: 14px;
  height: 14px;
  flex: none;
  stroke-width: 2;
}
</style>
