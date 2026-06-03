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

function hasRightContent(option: NormalizedOption | undefined): boolean {
  return Boolean(option?.rightLabel || option?.rightMeta)
}

function rightToneClass(option: NormalizedOption): string {
  return RIGHT_TONE_CLASSES[option.rightTone ?? 'neutral']
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
      :aria-invalid="controlInvalid || undefined"
      :aria-describedby="controlDescribedBy"
      :aria-required="controlRequired || undefined"
      :disabled="disabled"
      :class="[
        'ui-select__button focus-ring flex w-full items-center rounded-sm border bg-bg-surface text-left text-fg-default shadow-xs transition-colors duration-fast',
        controlInvalid ? 'border-danger' : 'border-default hover:border-strong hover:bg-bg-surface-alt',
        disabled &&
          'cursor-not-allowed bg-bg-sunken text-fg-disabled opacity-70 hover:border-default hover:bg-bg-sunken',
        sizeClass,
      ]"
      @click="setOpen(!open)"
      @keydown="onButtonKeydown"
    >
      <span
        class="min-w-0 flex-1 truncate"
        :class="!selectedOption && 'text-fg-disabled'"
      >
        {{ selectedLabel }}
      </span>
      <span
        v-if="hasRightContent(selectedOption)"
        class="pointer-events-none ml-2 flex shrink-0 items-center gap-1.5 pr-3"
      >
        <span
          v-if="selectedOption?.rightLabel"
          :class="['ui-select__right-chip', rightToneClass(selectedOption)]"
        >
          {{ selectedOption.rightLabel }}
        </span>
        <span
          v-if="selectedOption?.rightMeta"
          class="ui-select__right-meta text-fg-muted"
        >
          {{ selectedOption.rightMeta }}
        </span>
      </span>
      <span
        class="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-fg-subtle transition-transform duration-fast"
        :class="open && 'rotate-180'"
        aria-hidden="true"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </span>
    </button>

    <div
      v-if="open"
      class="absolute left-0 right-0 top-[calc(100%+4px)] z-popover max-h-72 overflow-y-auto rounded-md border border-default bg-bg-surface p-1 shadow-lg"
    >
      <div
        v-if="searchable"
        class="sticky top-0 z-10 bg-bg-surface p-1"
      >
        <UiInput
          :id="searchInputId"
          :model-value="searchQuery"
          type="search"
          size="sm"
          :placeholder="searchPlaceholder"
          aria-label="Search options"
          clearable
          @update:model-value="setSearchQuery"
          @keydown="onSearchKeydown"
        />
      </div>
      <div
        :id="listboxId"
        role="listbox"
        :aria-labelledby="controlId"
      >
        <template
          v-for="[group, groupOptions] in grouped"
          :key="group ?? '_none'"
        >
          <div
            v-if="group"
            class="px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-fg-subtle"
          >
            {{ group }}
          </div>
          <button
            v-for="option in groupOptions"
            :id="`${controlId}-${option.value}`"
            :key="option.value"
            type="button"
            role="option"
            :aria-selected="String(option.value) === String(modelValue ?? '')"
            :disabled="option.disabled"
            :class="[
              'flex min-h-8 w-full items-center justify-between gap-2 rounded-sm px-2.5 py-1.5 text-left text-sm transition-colors',
              String(option.value) === String(modelValue ?? '')
                ? 'bg-accent text-fg-on-accent'
                : normalized[activeIndex]?.value === option.value
                  ? 'bg-bg-surface-alt text-fg-strong'
                  : 'text-fg-default hover:bg-bg-surface-alt',
              option.disabled && 'cursor-not-allowed opacity-50',
            ]"
            @mouseenter="activeIndex = visibleIndexFor(option)"
            @click="selectOption(option)"
          >
            <span class="min-w-0 flex-1 truncate">{{ option.label }}</span>
            <span class="ml-auto flex shrink-0 items-center gap-1.5">
              <span
                v-if="option.rightLabel"
                :class="['ui-select__right-chip', rightToneClass(option)]"
              >
                {{ option.rightLabel }}
              </span>
              <span
                v-if="option.rightMeta"
                :class="[
                  'ui-select__right-meta',
                  String(option.value) === String(modelValue ?? '')
                    ? 'text-fg-on-accent'
                    : 'text-fg-muted',
                ]"
              >
                {{ option.rightMeta }}
              </span>
              <span
                v-if="String(option.value) === String(modelValue ?? '')"
                aria-hidden="true"
                class="shrink-0"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                >
                  <path d="m5 12 5 5 9-12" />
                </svg>
              </span>
            </span>
          </button>
        </template>
        <p
          v-if="filteredNormalized.length === 0"
          class="px-2.5 py-3 text-sm text-fg-muted"
        >
          {{ emptyLabel }}
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ui-select__right-chip {
  display: inline-flex;
  min-width: 0;
  max-width: 8.5rem;
  align-items: center;
  justify-content: center;
  border-radius: 3px;
  padding: 2px 6px;
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
}

.ui-select__right-meta {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
}
</style>
